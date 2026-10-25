#
# Qwen3-Omni conversational agent for Pipecat (local mic/speaker).
#
# Prerequisites:
#   1. vLLM-Omni voice server (thinker + talker + code2wav):
#        VOICE=1 GPU_ID=3 VLLM_PORT=8410 bash \
#          /home/aref.farhadipour/omni-annotate/realtime/run.sh
#   2. Pipecat venv with [local,silero] (+ optional [rnnoise] for noise suppression)
#
# Run:
#   cd /home/aref.farhadipour/pipecat/examples/qwen3-omni
#   source ../../.venv/bin/activate
#   cp env.example .env   # edit OMNI_BASE_URL if needed
#   python qwen3-omni-voice-agent-local.py
#

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# Local example modules live next to this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from omni_voice_service import Qwen3OmniVoiceService, Qwen3OmniVoiceSettings

from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

logger.remove()
logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"))


def _optional_noise_filter():
    if os.environ.get("OMNI_RNNOISE", "0") not in ("1", "true", "yes"):
        return None
    try:
        return RNNoiseFilter()
    except Exception as exc:
        logger.warning(f"RNNoise unavailable ({exc}); continuing without noise suppression")
        return None


def _vad_analyzer():
    """V5.2's engine switch: Silero's own VADIterator by default, pipecat's analyzer on demand.

    Both run the same weights. They differ in who makes the speech decision: VADIterator
    applies its own threshold, hysteresis band and minimum silence inside the model front
    end (see vad_iterator_analyzer), while SileroVADAnalyzer hands pipecat a raw
    probability and lets VADParams decide everything. OMNI_VAD_ENGINE=silero restores the
    V5.1 behaviour exactly.
    """
    engine = os.environ.get("OMNI_VAD_ENGINE", "vaditerator").strip().lower()
    params = VADParams(
        stop_secs=float(os.environ.get("OMNI_VAD_STOP_SECS", "0.4")),
    )
    if engine in ("silero", "pipecat"):
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        return SileroVADAnalyzer(params=params)
    try:
        from vad_iterator_analyzer import VADIteratorAnalyzer

        return VADIteratorAnalyzer(
            params=params,
            min_silence_duration_ms=int(
                os.environ.get("OMNI_VAD_ITER_MIN_SILENCE_MS", "100")
            ),
        )
    except Exception as exc:
        logger.warning(
            f"VADIterator unavailable ({exc}) — falling back to pipecat's SileroVADAnalyzer"
        )
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        return SileroVADAnalyzer(params=params)


async def main():
    base_url = os.environ.get("OMNI_BASE_URL", "http://127.0.0.1:8410/v1")
    model = os.environ.get("OMNI_MODEL", "qwen3-omni-voice")
    mode = os.environ.get("OMNI_MODE", "assistant")

    noise_filter = _optional_noise_filter()
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_filter=noise_filter,
        )
    )

    context = LLMContext(
        messages=[
            {
                "role": "developer",
                "content": (
                    "Introduce yourself briefly as a voice assistant powered by Qwen3-Omni. "
                    "Keep it to one short sentence."
                ),
            }
        ]
    )

    smart_turn = LocalSmartTurnAnalyzerV3(
        smart_turn_model_path=os.environ.get("OMNI_SMART_TURN_MODEL_PATH") or None,
        cpu_count=int(os.environ.get("OMNI_SMART_TURN_CPU_COUNT", "1")),
        params=SmartTurnParams(
            stop_secs=float(os.environ.get("OMNI_SMART_TURN_STOP_SECS", "3.0")),
            pre_speech_ms=float(os.environ.get("OMNI_SMART_TURN_PRE_SPEECH_MS", "500")),
            max_duration_secs=float(os.environ.get("OMNI_SMART_TURN_MAX_DURATION_SECS", "8.0")),
        ),
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=_vad_analyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy(enable_interruptions=True)],
                # wait_for_transcript=False: no STT in this pipeline (Omni takes the
                # audio directly), so the default would never see a transcript.
                stop=[
                    TurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=smart_turn, wait_for_transcript=False
                    )
                ],
            ),
            audio_idle_timeout=1.0,
        ),
    )

    omni = Qwen3OmniVoiceService(
        base_url=base_url,
        model=model,
        context=context,
        settings=Qwen3OmniVoiceSettings(
            mode=mode,  # type: ignore[arg-type]
            max_tokens=int(os.environ.get("OMNI_MAX_TOKENS", "200")),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            user_aggregator,
            omni,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner()
    await runner.add_workers(worker)

    logger.info(f"Qwen3-Omni voice agent — {base_url} model={model} mode={mode}")
    logger.info("Speak, pause briefly; barge-in by speaking while the bot talks.")

    await worker.queue_frames([LLMRunFrame()])
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
