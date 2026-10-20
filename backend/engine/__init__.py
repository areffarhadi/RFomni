import os

from .base import AnnotateContext, Engine  # noqa: F401
from .mock import MockEngine

_engine = None


def get_engine() -> Engine:
    """Pick the annotation engine. Only the mock engine exists for now; add a real one
    (e.g. OMNI_ENGINE=qwen_omni) here when the model is connected."""
    global _engine
    if _engine is None:
        name = os.environ.get("OMNI_ENGINE", "mock")
        if name != "mock":
            raise RuntimeError(f"Unknown engine '{name}'. Available: mock")
        _engine = MockEngine()
    return _engine
