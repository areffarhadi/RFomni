"""HTTP client for Qwen3-Omni served via vLLM-Omni (OpenAI-compatible chat/completions).

Two-hop speech pipeline (matches omni-annotate realtime/server.py):
  hop 1 — audio -> ASR + LID   (modalities=["text"])
  hop 2 — text  -> speech      (modalities=["text","audio"], streamed WAV chunks)
"""

from __future__ import annotations

import base64
import io
import json
import os
import wave
from dataclasses import dataclass
from typing import AsyncIterator, Literal

import httpx

VoiceMode = Literal["assistant", "translate_en", "translate_de", "say", "annotator"]

ANNOTATE_TASKS = ["ASR", "NER", "EMO", "GEN", "LID", "EVENT", "QUALITY"]
ANNOTATE_PROMPT = (
    "JSON only. audio annotation Tasks: "
    + ", ".join(ANNOTATE_TASKS)
    + ". Keys: "
    + ", ".join(ANNOTATE_TASKS)
    + "."
)

SPEAKABLE = {
    "zh": "Chinese",
    "en": "English",
    "fr": "French",
    "de": "German",
    "ru": "Russian",
    "it": "Italian",
    "es": "Spanish",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
}
SPEAKABLE_FALLBACK = "English"

# The languages a restricted session accepts. Speech outside the set is not transcribed
# into it — the speaker is asked to switch instead, because a mistranscription into a
# language the speaker did not use is worse than saying no.
LANG_SET = tuple(
    c for c in (
        norm.strip().lower()
        for norm in os.environ.get("OMNI_LANG_SET", "en,de,fr,es,pt").split(",")
    ) if c
)

VOICE_MODES = {
    "translate_en": "Translate into English. Reply with only the translation:\n\n",
    "translate_de": "Translate into German. Reply with only the translation:\n\n",
    "assistant": (
        "You are a spoken voice assistant. Reply in at most two short sentences "
        "of plain conversational speech. No markdown, no lists, no headings. "
        "{lang}\n\n"
    ),
    "say": (
        "Repeat the following text exactly as written. Output only that text, "
        "with no preamble, no quotation marks and no commentary.\n\n"
    ),
}
_MODE_LANG = {"translate_en": "en", "translate_de": "de"}


def pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def pcm_to_wav_b64(pcm: bytes, sample_rate: int = 16000) -> str:
    return base64.b64encode(pcm_to_wav(pcm, sample_rate)).decode()


def decode_wav_b64(b64: str) -> tuple[bytes, int, int]:
    """Return (pcm_bytes, sample_rate, num_channels)."""
    raw = base64.b64decode(b64)
    with wave.open(io.BytesIO(raw), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        pcm = w.readframes(w.getnframes())
    return pcm, sr, ch


def norm_lid(lid: str) -> str:
    return (lid or "").strip().lower().replace("_", "-").split("-")[0]


def assistant_lang(lid: str, fallback: str = SPEAKABLE_FALLBACK) -> str:
    return SPEAKABLE.get(norm_lid(lid), fallback)


def assistant_prompt(lid: str, fallback: str = SPEAKABLE_FALLBACK, *, strict: bool = False) -> str:
    lang = assistant_lang(lid, fallback)
    code = norm_lid(lid)
    if strict and code in SPEAKABLE:
        instr = (
            f"CRITICAL: You must speak ONLY {lang}. "
            f"Every word of your reply must be {lang}. "
            f"Do not mix languages. Do not answer in any other language."
        )
    elif code in SPEAKABLE:
        instr = f"Reply in {lang}."
    else:
        instr = (
            f"Reply in {fallback}, whatever language the user wrote in. "
            f"This voice cannot pronounce their language, so {fallback} is required."
        )
    return VOICE_MODES["assistant"].format(lang=instr)


def voice_prompt(
    mode: VoiceMode, text: str, lid: str = "", *, strict_lang: bool = False
) -> str:
    if mode == "assistant":
        return assistant_prompt(lid, strict=strict_lang) + text
    return VOICE_MODES.get(mode, VOICE_MODES["assistant"]) + text


def lang_names(codes, conjunction: str = "and") -> str:
    """"English, German and French" — for prompts and spoken messages."""
    names = [SPEAKABLE.get(norm_lid(c), c) for c in codes]
    if len(names) <= 1:
        return names[0] if names else SPEAKABLE_FALLBACK
    return ", ".join(names[:-1]) + f" {conjunction} " + names[-1]


def asr_prompt_for(codes) -> str:
    """ASR instruction for a session restricted to ``codes`` (empty = unrestricted)."""
    codes = tuple(codes)
    if not codes:
        return "JSON only. audio annotation Tasks: ASR, LID. Keys: ASR, LID."
    if len(codes) == 1:
        code = norm_lid(codes[0])
        name = SPEAKABLE.get(code, code)
        return (
            f"The speaker is speaking {name} ({code}). "
            f"Transcribe the speech verbatim in {name}. "
            f'JSON only. Keys: ASR, LID. Set LID to "{code}".'
        )
    listed = ", ".join(f"{SPEAKABLE.get(norm_lid(c), c)} ({norm_lid(c)})" for c in codes)
    return (
        f"The speaker is using one of these languages: {listed}. "
        "Transcribe the speech verbatim in whichever one it is, and set LID to that "
        "language's code. Do not transcribe or translate into any other language. "
        "JSON only. Keys: ASR, LID."
    )


def effective_mode(mode: VoiceMode, lid: str) -> VoiceMode:
    if lid and _MODE_LANG.get(mode) == norm_lid(lid):
        return "say"
    return mode


@dataclass
class OmniClientConfig:
    base_url: str = "http://127.0.0.1:8410/v1"
    model: str = "qwen3-omni-voice"
    timeout_s: float = 300.0
    speakable_fallback: str = SPEAKABLE_FALLBACK
    # Qwen3-Omni talker voices (from talker_config.speaker_id)
    speaker: str = "ethan"


OMNI_SPEAKERS = ("ethan", "chelsie", "aiden")


def normalize_speaker(name: str | None, default: str = "ethan") -> str:
    s = (name or default).strip().lower()
    return s if s in OMNI_SPEAKERS else default


_http_client: httpx.AsyncClient | None = None


def _http_limits() -> httpx.Limits:
    return httpx.Limits(
        max_connections=int(os.environ.get("OMNI_HTTP_MAX_CONNECTIONS", "32")),
        max_keepalive_connections=int(os.environ.get("OMNI_HTTP_KEEPALIVE", "16")),
    )


def shared_http_client(timeout: float) -> httpx.AsyncClient:
    """Reuse connections across concurrent WebRTC sessions."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=timeout, limits=_http_limits())
    return _http_client


class Qwen3OmniClient:
    def __init__(self, config: OmniClientConfig | None = None):
        self.config = config or OmniClientConfig()
        self._url = self.config.base_url.rstrip("/") + "/chat/completions"

    async def transcribe(
        self,
        pcm: bytes,
        sample_rate: int = 16000,
        *,
        force_lang: str = "",
        allowed_langs=(),
    ) -> tuple[str, str]:
        """Hop 1: audio -> (transcript, lid).

        ``allowed_langs`` narrows what the session accepts. Naming the candidates up
        front beats brittle open-set detection, and with a single code LID is pinned to
        it outright. Empty means unrestricted.
        """
        wav_b64 = pcm_to_wav_b64(pcm, sample_rate)
        codes = tuple(norm_lid(c) for c in (allowed_langs or ()) if norm_lid(c))
        if not codes and norm_lid(force_lang):
            codes = (norm_lid(force_lang),)
        forced = codes[0] if len(codes) == 1 else ""
        asr_prompt = asr_prompt_for(codes)
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "audio_url",
                            "audio_url": {"url": f"data:audio/wav;base64,{wav_b64}"},
                        },
                        {
                            "type": "text",
                            "text": asr_prompt,
                        },
                    ],
                }
            ],
            "max_tokens": 200,
            "modalities": ["text"],
            "stream": False,
        }
        client = shared_http_client(self.config.timeout_s)
        r = await client.post(self._url, json=payload)
        r.raise_for_status()
        txt = (r.json()["choices"][0]["message"].get("content") or "").strip()
        try:
            obj = json.loads(txt)
            asr = (obj.get("ASR") or "").strip()
            lid = (obj.get("LID") or "").strip()
        except json.JSONDecodeError:
            asr, lid = txt, ""
        if forced and forced in SPEAKABLE:
            lid = forced
        return asr, lid

    async def annotate(self, pcm: bytes, sample_rate: int = 16000) -> dict:
        """Full unified_caps annotation (ASR, NER, EMO, GEN, LID, EVENT, QUALITY)."""
        wav_b64 = pcm_to_wav_b64(pcm, sample_rate)
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "audio_url",
                            "audio_url": {"url": f"data:audio/wav;base64,{wav_b64}"},
                        },
                        {"type": "text", "text": ANNOTATE_PROMPT},
                    ],
                }
            ],
            "max_tokens": 400,
            "modalities": ["text"],
            "stream": False,
        }
        client = shared_http_client(self.config.timeout_s)
        r = await client.post(self._url, json=payload)
        r.raise_for_status()
        txt = (r.json()["choices"][0]["message"].get("content") or "").strip()
        try:
            obj = json.loads(txt)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        # Bare transcript fallback
        return {"ASR": txt, "LID": ""}

    async def translate_to_en(self, text: str, *, source_lang: str = "") -> str:
        """Text→English (same pattern as omni-annotate /translate)."""
        text = (text or "").strip()
        if not text:
            return ""
        if norm_lid(source_lang) == "en":
            return text
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Translate the following text into English. "
                        "Reply with only the English translation.\n\n" + text
                    ),
                }
            ],
            "max_tokens": 256,
            "modalities": ["text"],
            "stream": False,
        }
        client = shared_http_client(self.config.timeout_s)
        r = await client.post(self._url, json=payload)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()

    async def vision_reply(
        self,
        prompt: str,
        *,
        image_jpeg_b64: str | None = None,
        max_tokens: int = 120,
    ) -> str:
        """Text (+ optional camera JPEG) → text. Used for reaction / scene questions."""
        content: list = []
        if image_jpeg_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_jpeg_b64}"},
                }
            )
        content.append({"type": "text", "text": prompt})
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "modalities": ["text"],
            "stream": False,
        }
        client = shared_http_client(self.config.timeout_s)
        r = await client.post(self._url, json=payload)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()

    async def detect_lang_text(self, text: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Reply with only the ISO 639-1 language code of the following text — "
                        "two lowercase letters, nothing else.\n\n" + text[:400]
                    ),
                }
            ],
            "max_tokens": 8,
            "modalities": ["text"],
            "stream": False,
        }
        try:
            client = shared_http_client(30)
            r = await client.post(self._url, json=payload)
            r.raise_for_status()
            code = (r.json()["choices"][0]["message"].get("content") or "").strip()
            code = norm_lid(code)
            return code if code.isalpha() and 2 <= len(code) <= 3 else ""
        except Exception:
            return ""

    async def speak_stream(
        self, prompt: str, *, max_tokens: int = 200, speaker: str | None = None
    ) -> AsyncIterator[tuple[str, str]]:
        """Hop 2: text -> speech. Yields ('text', str) or ('audio', base64-wav)."""
        spk = normalize_speaker(speaker or self.config.speaker)
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "modalities": ["text", "audio"],
            "speaker": spk,
            "stream": True,
        }
        client = shared_http_client(self.config.timeout_s)
        async with client.stream("POST", self._url, json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode()[:300]
                raise RuntimeError(body)
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                piece = (choices[0].get("delta") or {}).get("content")
                if not piece:
                    continue
                is_audio = (
                    obj.get("modality") == "audio"
                    or str(piece).startswith("UklGR")
                )
                yield ("audio" if is_audio else "text"), piece
