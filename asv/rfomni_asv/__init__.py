"""RFOMNI ASV — speaker verification for voice agents.

Two things, sharing one voiceprint:

* the **utterance gate** — answer only the enrolled speaker (:class:`SpeakerGate`)
* the **barge-in gate** — only the enrolled speaker may interrupt
  (:class:`BargeInController`)

Both talk to :mod:`rfomni_asv.service`, which holds the model. See the README.
"""

from .barge import BargeDecision, BargeInController
from .gate import GateError, SpeakerGate

__all__ = ["SpeakerGate", "GateError", "BargeInController", "BargeDecision"]
__version__ = "1.0.0"
