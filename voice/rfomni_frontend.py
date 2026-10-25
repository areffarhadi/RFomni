"""Serve the rebranded RFOMNI WebRTC client."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

_DIST = Path(__file__).resolve().parent / "client" / "dist"


def setup_rfomni_frontend_routes(app: FastAPI) -> None:
    if not _DIST.is_dir():
        raise RuntimeError(
            f"RFOMNI client not built at {_DIST}. "
            "Run prepare_rfomni_client.sh first."
        )
    app.mount("/client", StaticFiles(directory=str(_DIST), html=True))

    @app.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse(url="/client/")


def patch_runner_frontend() -> None:
    """Use RFOMNI client instead of default Pipecat Playground UI."""
    from pipecat.runner import run as run_module

    run_module._setup_frontend_routes = setup_rfomni_frontend_routes  # type: ignore[attr-defined]
