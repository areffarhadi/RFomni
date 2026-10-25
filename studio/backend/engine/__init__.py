import os

from .base import AnnotateContext, Engine  # noqa: F401
from .mock import MockEngine
from .omni import OmniEngine, UnsupportedTask  # noqa: F401

_engine = None


def get_engine() -> Engine:
    """Pick the annotation engine: OMNI_ENGINE=omni (default, the fine-tuned thinker on
    vLLM-omni) or mock (placeholder values, no model needed)."""
    global _engine
    if _engine is None:
        name = os.environ.get("OMNI_ENGINE", "omni")
        engines = {"omni": OmniEngine, "mock": MockEngine}
        if name not in engines:
            raise RuntimeError(f"Unknown engine '{name}'. Available: {', '.join(engines)}")
        _engine = engines[name]()
    return _engine
