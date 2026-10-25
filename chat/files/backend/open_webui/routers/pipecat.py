"""Proxy Pipecat WebRTC signaling so Open WebUI Voice mode can use Omni RFOMNI.

Browser talks only to Open WebUI (already trusted HTTPS). Media still goes
peer-to-peer via WebRTC to the Pipecat agent; only /start and SDP/ICE are proxied.
"""

from __future__ import annotations

import logging
import os
import ssl
from typing import Any, Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()

PIPECAT_BASE_URL = os.environ.get("PIPECAT_BASE_URL", "https://127.0.0.1:7860").rstrip("/")
PIPECAT_ENABLED = os.environ.get("PIPECAT_ENABLED", "true").lower() in {"1", "true", "yes"}
PIPECAT_INSECURE_SSL = os.environ.get("PIPECAT_INSECURE_SSL", "true").lower() in {
    "1",
    "true",
    "yes",
}


def _ssl_ctx() -> Optional[ssl.SSLContext]:
    if not PIPECAT_INSECURE_SSL:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@router.get("/status")
async def pipecat_status(user=Depends(get_verified_user)):
    if not PIPECAT_ENABLED:
        return {"enabled": False, "upstream": None, "ready": False}
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{PIPECAT_BASE_URL}/status",
                ssl=_ssl_ctx(),
            ) as resp:
                body = await resp.json(content_type=None)
                return {
                    "enabled": True,
                    "upstream": PIPECAT_BASE_URL,
                    "ready": resp.status == 200,
                    "status": body,
                }
    except Exception as e:
        log.warning("Pipecat status check failed: %s", e)
        return {
            "enabled": True,
            "upstream": PIPECAT_BASE_URL,
            "ready": False,
            "error": str(e),
        }


@router.api_route("/start", methods=["POST"])
@router.api_route("/sessions/{session_id}/api/offer", methods=["POST", "PATCH"])
@router.api_route("/api/offer", methods=["POST", "PATCH"])
async def pipecat_proxy(
    request: Request,
    session_id: Optional[str] = None,
    user=Depends(get_verified_user),
):
    if not PIPECAT_ENABLED:
        raise HTTPException(status_code=503, detail="Pipecat voice backend disabled")

    path = request.url.path
    # Strip our mount prefix /api/v1/pipecat
    marker = "/api/v1/pipecat"
    idx = path.find(marker)
    upstream_path = path[idx + len(marker) :] if idx >= 0 else path
    if not upstream_path.startswith("/"):
        upstream_path = "/" + upstream_path

    url = f"{PIPECAT_BASE_URL}{upstream_path}"
    body = await request.body()
    headers = {
        "Content-Type": request.headers.get("content-type", "application/json"),
    }

    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                request.method,
                url,
                data=body if body else None,
                headers=headers,
                ssl=_ssl_ctx(),
            ) as resp:
                content = await resp.read()
                return Response(
                    content=content,
                    status_code=resp.status,
                    media_type=resp.headers.get("content-type", "application/json"),
                )
    except Exception as e:
        log.exception("Pipecat proxy error for %s", url)
        raise HTTPException(status_code=502, detail=f"Pipecat upstream error: {e}") from e
