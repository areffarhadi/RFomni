#!/usr/bin/env python3
"""Thin proxy in front of Omni vLLM for Open WebUI.

Open WebUI does not send modalities=["text"], so the voice stack runs the talker
path and streams long meaningless codes into the chat. That fills the 4096
context and breaks the next turn.

This proxy:
  - forces modalities=["text"] (skip talker for chat text)
  - caps max_tokens (default 512)
  - strips tools/tool_choice, which the voice backend is not started to support
  - optionally trims old messages if the request looks oversized
  - adds the Omni Studio assistant as one more model ("omni-studio"); its chats go to the
    studio, which shares one plan with the studio panel and the voice agent
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

UPSTREAM = os.environ.get("OMNI_UPSTREAM", "http://127.0.0.1:8400")
MAX_TOKENS = int(os.environ.get("OMNI_MAX_TOKENS", "512"))
HOST = os.environ.get("PROXY_HOST", "0.0.0.0")
PORT = int(os.environ.get("PROXY_PORT", "8401"))
STUDIO = os.environ.get("OMNI_STUDIO_URL", "https://127.0.0.1:8710").rstrip("/")
STUDIO_MODEL = "omni-studio"
TASK_MODEL = os.environ.get("OMNI_TASK_MODEL", "mix87")  # titles/tags for assistant chats
# names shown on the chat page; the ids stay, so saved chats and default-model settings keep working
DISPLAY_NAMES = {"mix87": os.environ.get("OMNI_MODEL_DISPLAY_NAME", "RFomni")}

app = FastAPI(title="omni-openwebui-proxy")
client = httpx.AsyncClient(base_url=UPSTREAM, timeout=None)
# self-signed LAN certificate; the studio knows the chat user from Open WebUI's signed header
studio = httpx.AsyncClient(base_url=STUDIO, timeout=httpx.Timeout(300, connect=5), verify=False)
USER_JWT = "x-openwebui-user-jwt"


def _is_owui_task(body: dict[str, Any]) -> bool:
    """Open WebUI's background prompts (title, tags, follow-ups) start with "### Task:"."""
    for m in reversed(body.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            return isinstance(c, str) and c.lstrip().startswith("### Task:")
    return False


async def _studio_models() -> list:
    try:
        r = await studio.get("/api/agent/v1/models")
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception:
        return []  # studio down: the chat page simply does not list the assistant


def sanitize_chat_body(body: dict[str, Any]) -> dict[str, Any]:
    body = dict(body)
    # Critical: skip talker/code2wav so chat stays coherent text
    body["modalities"] = ["text"]

    mt = body.get("max_tokens")
    if mt is None or not isinstance(mt, int) or mt <= 0 or mt > MAX_TOKENS:
        body["max_tokens"] = MAX_TOKENS
    else:
        body["max_tokens"] = min(mt, MAX_TOKENS)

    # Keep recent turns only — Open WebUI can re-send a huge poisoned history
    messages = body.get("messages")
    if isinstance(messages, list) and len(messages) > 12:
        system = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
        rest = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
        body["messages"] = system + rest[-10:]

    # Open WebUI sends tool_choice="auto" whenever plugins are enabled, but the voice
    # backend is served without --enable-auto-tool-choice/--tool-call-parser (the voice
    # launcher needs the 3-stage talker config, and the realtime launcher that provides
    # it sets no tool flags). vLLM rejects the whole request with a 400, so text chat
    # dies the moment a voice-capable backend is behind this proxy. Drop the tool fields:
    # without a parser the model cannot emit tool calls anyway, and leaving the schemas
    # in the prompt would only burn context.
    # Pop unconditionally: vLLM also defaults to auto tool choice when `tools` is
    # present on its own, so removing only `tool_choice` still fails.
    for key in ("tool_choice", "tools", "parallel_tool_calls", "functions", "function_call"):
        body.pop(key, None)

    # Soft-trim any single assistant message that already looks like runaway junk
    msgs = body.get("messages") or []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str) and len(c) > 4000:
            m["content"] = c[:1500] + "\n…[truncated]…"
    return body


@app.get("/health")
async def health():
    try:
        r = await client.get("/v1/models")
        return {"proxy": "ok", "upstream": r.status_code}
    except Exception as e:
        return {"proxy": "ok", "upstream": str(e)}


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def forward(path: str, request: Request):
    url = f"/v1/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}}

    if request.method == "GET" and path.rstrip("/") == "models":
        upstream = await client.get(url)
        if upstream.status_code != 200:
            return Response(content=upstream.content, status_code=upstream.status_code,
                            media_type=upstream.headers.get("content-type"))
        data = upstream.json()
        for m in data.get("data", []):
            if m.get("id") in DISPLAY_NAMES:
                m["name"] = DISPLAY_NAMES[m["id"]]
        data["data"] = data.get("data", []) + await _studio_models()
        return data

    if request.method == "POST" and path.rstrip("/").endswith("chat/completions"):
        raw = await request.json()
        if raw.get("model") == STUDIO_MODEL:
            if not _is_owui_task(raw):
                r = await studio.post("/api/agent/v1/chat/completions", json=raw,
                                      headers={USER_JWT: request.headers.get(USER_JWT, "")})
                return Response(content=r.content, status_code=r.status_code,
                                media_type=r.headers.get("content-type"))
            raw["model"] = TASK_MODEL
        body = sanitize_chat_body(raw)
        stream = bool(body.get("stream"))
        req = client.build_request("POST", url, headers=headers, json=body)
        upstream = await client.send(req, stream=True)
        if stream:
            async def gen():
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()
            return StreamingResponse(
                gen(),
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "text/event-stream"),
                headers={
                    k: v
                    for k, v in upstream.headers.items()
                    if k.lower()
                    in {"content-type", "cache-control", "x-request-id"}
                },
            )
        content = await upstream.aread()
        await upstream.aclose()
        return Response(content=content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type"))

    # everything else (models list, etc.)
    content = await request.body()
    req = client.build_request(request.method, url, headers=headers, content=content)
    upstream = await client.send(req, stream=False)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


if __name__ == "__main__":
    print(f"[proxy] upstream={UPSTREAM} max_tokens={MAX_TOKENS} listen={HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
