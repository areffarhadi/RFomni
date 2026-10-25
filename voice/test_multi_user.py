#!/usr/bin/env python3
"""Simulate multi-user load on the Qwen3-Omni Pipecat stack.

Tests:
  1. Multiple WebRTC session registrations (/start)
  2. Concurrent Omni speak streams (what each user triggers on a turn)
  3. Overlapping full turns (speak + second speak while first in flight)

Usage:
  python test_multi_user.py
  python test_multi_user.py --users 4 --agent https://127.0.0.1:7860
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omni_client import Qwen3OmniClient, OmniClientConfig


@dataclass
class TurnResult:
    user_id: int
    ok: bool
    latency_s: float
    text: str = ""
    audio_bytes: int = 0
    error: str = ""


@dataclass
class SuiteResult:
    name: str
    results: list[TurnResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def _ssl_ctx(insecure: bool) -> ssl.SSLContext | None:
    if not insecure:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def check_vllm(base_url: str) -> bool:
    url = base_url.rstrip("/").removesuffix("/v1") + "/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def test_webrtc_sessions(agent_base: str, n: int, insecure: bool) -> SuiteResult:
    """Register N WebRTC sessions via POST /start (Pipecat prebuilt flow)."""
    suite = SuiteResult(name=f"webrtc_session_register x{n}")
    url = agent_base.rstrip("/") + "/start"
    body = json.dumps(
        {
            "createDailyRoom": False,
            "enableDefaultIceServers": True,
            "transport": "webrtc",
        }
    ).encode()
    ctx = _ssl_ctx(insecure)

    for i in range(n):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                data = json.loads(resp.read().decode())
            sid = data.get("sessionId") or data.get("session_id")
            suite.results.append(
                TurnResult(
                    user_id=i,
                    ok=bool(sid),
                    latency_s=time.perf_counter() - t0,
                    text=str(sid or "")[:36],
                )
            )
        except Exception as exc:
            suite.results.append(
                TurnResult(
                    user_id=i,
                    ok=False,
                    latency_s=time.perf_counter() - t0,
                    error=str(exc)[:200],
                )
            )
    return suite


async def _speak_turn(client: Qwen3OmniClient, user_id: int, prompt: str) -> TurnResult:
    t0 = time.perf_counter()
    text_parts: list[str] = []
    audio_bytes = 0
    try:
        import base64

        async for kind, piece in client.speak_stream(prompt, max_tokens=80):
            if kind == "text":
                text_parts.append(piece)
            else:
                try:
                    audio_bytes += len(base64.b64decode(piece))
                except Exception:
                    audio_bytes += len(piece)
        return TurnResult(
            user_id=user_id,
            ok=audio_bytes > 1000,
            latency_s=time.perf_counter() - t0,
            text="".join(text_parts)[:80],
            audio_bytes=audio_bytes,
        )
    except Exception as exc:
        return TurnResult(
            user_id=user_id,
            ok=False,
            latency_s=time.perf_counter() - t0,
            error=str(exc)[:200],
        )


async def test_concurrent_speak(base_url: str, n: int, stagger_ms: int) -> SuiteResult:
    """Simulate N users finishing a turn and requesting speech at once."""
    label = "concurrent_speak" if stagger_ms == 0 else f"staggered_speak_{stagger_ms}ms"
    suite = SuiteResult(name=f"{label} x{n}")
    client = Qwen3OmniClient(OmniClientConfig(base_url=base_url))

    async def run_user(i: int) -> TurnResult:
        if stagger_ms:
            await asyncio.sleep(i * stagger_ms / 1000)
        prompt = (
            "You are a spoken voice assistant. Reply in one short sentence. "
            f"User {i} asks: What is two plus two?"
        )
        return await _speak_turn(client, i, prompt)

    suite.results = list(await asyncio.gather(*[run_user(i) for i in range(n)]))
    return suite


async def test_overlapping_turns(base_url: str) -> SuiteResult:
    """User 0 starts a long reply; user 1 starts before user 0 finishes."""
    suite = SuiteResult(name="overlapping_turns x2")
    client = Qwen3OmniClient(OmniClientConfig(base_url=base_url))

    async def long_turn() -> TurnResult:
        prompt = (
            "You are a spoken voice assistant. Reply in exactly three short sentences "
            "about the weather."
        )
        return await _speak_turn(client, 0, prompt)

    async def short_after_delay() -> TurnResult:
        await asyncio.sleep(0.3)
        return await _speak_turn(
            client, 1, "You are a spoken voice assistant. Say hello in one sentence."
        )

    suite.results = list(await asyncio.gather(long_turn(), short_after_delay()))
    return suite


def print_suite(suite: SuiteResult) -> None:
    print(f"\n=== {suite.name} ===")
    print(f"  ok={suite.ok_count}  fail={suite.fail_count}  total={len(suite.results)}")
    for r in suite.results:
        status = "OK" if r.ok else "FAIL"
        extra = f' text="{r.text}"' if r.text else ""
        audio = f" audio={r.audio_bytes}B" if r.audio_bytes else ""
        err = f" err={r.error}" if r.error else ""
        print(f"  user-{r.user_id}: {status}  {r.latency_s:.2f}s{extra}{audio}{err}")


async def main() -> int:
    p = argparse.ArgumentParser(description="Multi-user simulation for Pipecat Omni stack")
    p.add_argument("--users", type=int, default=3, help="Concurrent virtual users")
    p.add_argument("--omni-base", default="http://127.0.0.1:8410/v1")
    p.add_argument("--agent", default="https://127.0.0.1:7860")
    p.add_argument("--insecure", action="store_true", default=True)
    p.add_argument("--skip-agent", action="store_true")
    args = p.parse_args()

    print("Multi-user simulation — Qwen3-Omni / Pipecat")
    print(f"  vLLM:  {args.omni_base}")
    print(f"  agent: {args.agent}")
    print(f"  users: {args.users}")

    vllm_before = check_vllm(args.omni_base)
    print(f"\nvLLM before tests: {'UP' if vllm_before else 'DOWN'}")
    if not vllm_before:
        print("ERROR: vLLM not reachable — start the voice stack first.")
        return 1

    suites: list[SuiteResult] = []

    if not args.skip_agent:
        suites.append(test_webrtc_sessions(args.agent, args.users, args.insecure))

    # Baseline: single user
    suites.append(await test_concurrent_speak(args.omni_base, 1, 0))

    # Concurrent: all users at once (hardest for vLLM orchestrator)
    suites.append(await test_concurrent_speak(args.omni_base, args.users, 0))

    # Staggered: 500ms apart (closer to polite multi-user)
    if args.users > 1:
        suites.append(await test_concurrent_speak(args.omni_base, args.users, 500))

    if args.users >= 2:
        suites.append(await test_overlapping_turns(args.omni_base))

    for s in suites:
        print_suite(s)

    vllm_after = check_vllm(args.omni_base)
    print(f"\nvLLM after tests: {'UP' if vllm_after else 'DOWN *** CRASHED ***'}")

    total_fail = sum(s.fail_count for s in suites)
    if not vllm_after:
        print("\nVERDICT: vLLM died under multi-user simulation — not safe for concurrent users.")
        return 2
    if total_fail:
        print(f"\nVERDICT: {total_fail} failed request(s) — concurrent use is unreliable.")
        return 1
    print("\nVERDICT: All simulated turns succeeded; vLLM stayed up.")
    print("Note: this tests the shared Omni backend, not real browser WebRTC audio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
