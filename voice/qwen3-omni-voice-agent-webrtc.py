#
# Qwen3-Omni Pipecat agent — V5.3 (Smart Turn v3 end-of-turn).
# Browser mic/speaker/camera via Small WebRTC. Same as V5.2 for identity / VAD start,
# but end-of-turn is LocalSmartTurnAnalyzerV3 instead of SpeechTimeout.
#

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent))

# BEFORE the local imports, not after. speaker_gate and auto_enroll read their settings
# at import time, so a .env loaded further down arrives too late to be seen: the agent
# then runs on code defaults while .env claims otherwise. Caught on 2026-09-17, when a
# V5.1 session adopted ecapa512's shipped 0.50 and refused the owner at 0.436 although
# env.tuned pinned 0.37.
load_dotenv(override=True)

from camera_tap import CameraTap
from local_talking_avatar import build_avatar_processor
from omni_concurrency import max_concurrent_turns
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
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import maybe_capture_participant_camera
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from rfomni_frontend import patch_runner_frontend

patch_runner_frontend()


def _optional_noise_filter():
    if os.environ.get("OMNI_RNNOISE", "0") not in ("1", "true", "yes"):
        return None
    try:
        return RNNoiseFilter()
    except Exception as exc:
        logger.warning(f"RNNoise unavailable ({exc})")
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
        confidence=float(os.environ.get("OMNI_VAD_CONFIDENCE", "0.7")),
        start_secs=float(os.environ.get("OMNI_VAD_START_SECS", "0.2")),
        stop_secs=float(os.environ.get("OMNI_VAD_STOP_SECS", "0.4")),
        min_volume=float(os.environ.get("OMNI_VAD_MIN_VOLUME", "0.55")),
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


def _smart_turn_analyzer() -> LocalSmartTurnAnalyzerV3:
    """V5.3 end-of-turn: Local Smart Turn v3 (ONNX), not silence timeout.

    V5.2 kept SpeechTimeoutUserTurnStopStrategy. This version keeps the same VAD for
    *start* of speech, and hands *stop* to pipecat's bundled smart-turn-v3.2 model.
    """
    params = SmartTurnParams(
        stop_secs=float(os.environ.get("OMNI_SMART_TURN_STOP_SECS", "3.0")),
        pre_speech_ms=float(os.environ.get("OMNI_SMART_TURN_PRE_SPEECH_MS", "500")),
        max_duration_secs=float(os.environ.get("OMNI_SMART_TURN_MAX_DURATION_SECS", "8.0")),
    )
    model_path = os.environ.get("OMNI_SMART_TURN_MODEL_PATH") or None
    cpu_count = int(os.environ.get("OMNI_SMART_TURN_CPU_COUNT", "1"))
    return LocalSmartTurnAnalyzerV3(
        smart_turn_model_path=model_path,
        cpu_count=cpu_count,
        params=params,
    )


def _user_turn_params() -> LLMUserAggregatorParams:
    return LLMUserAggregatorParams(
        vad_analyzer=_vad_analyzer(),
        user_turn_strategies=UserTurnStrategies(
            # Interruptions are broadcast by Qwen3OmniVoiceService instead, which holds
            # them until the interrupter is confirmed to be the enrolled speaker. With
            # nobody enrolled it interrupts immediately, as this strategy would have.
            start=[VADUserTurnStartStrategy(enable_interruptions=False)],
            stop=[
                # wait_for_transcript=False: there is no STT in this pipeline — Omni
                # consumes the audio itself — so no TranscriptionFrame ever arrives and
                # the default would hold every turn until user_turn_stop_timeout.
                TurnAnalyzerUserTurnStopStrategy(
                    turn_analyzer=_smart_turn_analyzer(), wait_for_transcript=False
                ),
            ],
        ),
        user_turn_stop_timeout=float(os.environ.get("OMNI_TURN_STOP_TIMEOUT", "8.0")),
    )


def _video_size() -> tuple[int, int]:
    return (
        int(os.environ.get("OMNI_AVATAR_WIDTH", "512")),
        int(os.environ.get("OMNI_AVATAR_HEIGHT", "512")),
    )


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    base_url = os.environ.get("OMNI_BASE_URL", "http://127.0.0.1:8400/v1")
    model = os.environ.get("OMNI_MODEL", "qwen3-omni-voice")
    # A client can open the session in a given mode ({"mode": "studio"} in the /start body),
    # so the first turn already runs in it and the greeting is skipped where it does not fit.
    body = getattr(runner_args, "body", None)
    mode = (body.get("mode") if isinstance(body, dict) else None) or os.environ.get("OMNI_MODE", "assistant")
    session_id = getattr(runner_args, "session_id", None) or ""
    vw, vh = _video_size()

    context = LLMContext(
        messages=[
            {
                "role": "developer",
                "content": (
                    "Introduce yourself briefly as RFomni, a helpful voice assistant. "
                    "Say your name as R F omni (the letters R and F, then the word omni). "
                    "Do not mention Qwen, Omni, or any model name. One short sentence."
                ),
            }
        ]
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=_user_turn_params(),
    )

    status_sink = {"rtvi": None}

    async def on_status(payload: dict):
        rtvi = status_sink.get("rtvi")
        if rtvi is not None:
            await rtvi.send_server_message(payload)

    omni = Qwen3OmniVoiceService(
        base_url=base_url,
        model=model,
        context=context,
        session_id=session_id,
        on_status=on_status,
        studio_auth=(body.get("studio_auth") or "") if isinstance(body, dict) else "",
        settings=Qwen3OmniVoiceSettings(
            mode=mode,  # type: ignore[arg-type]
            max_tokens=int(os.environ.get("OMNI_MAX_TOKENS", "200")),
            context_turns=int(os.environ.get("OMNI_CONTEXT_TURNS", "12")),
            vision_enabled=os.environ.get("OMNI_VISION", "1") not in ("0", "false", "no"),
            speaker=os.environ.get("OMNI_SPEAKER", "ethan"),
        ),
    )

    def _on_cam_jpeg(b64: str):
        omni._latest_jpeg_b64 = b64

    camera_tap = CameraTap(on_jpeg=_on_cam_jpeg)
    avatar, avatar_kind = build_avatar_processor()

    stages = [
        transport.input(),
        camera_tap,  # before aggregator — it would otherwise swallow UserImageRawFrame
        user_aggregator,
        omni,
    ]
    if avatar is not None:
        stages.append(avatar)
    stages.extend([transport.output(), assistant_aggregator])

    pipeline = Pipeline(stages)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    status_sink["rtvi"] = worker.rtvi

    @worker.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, message):
        # message: RTVI.ClientMessage with .type / .data
        mtype = getattr(message, "type", None) or getattr(message, "t", None)
        data = getattr(message, "data", None) or {}
        if mtype == "set-mode":
            mode_val = (data or {}).get("mode") if isinstance(data, dict) else None
            try:
                applied = omni.set_mode(str(mode_val or ""))
                await rtvi.send_server_message({"type": "rfomni-mode", "mode": applied})
                await rtvi.send_server_message(omni.memory_stats())
            except Exception as exc:
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})
        elif mtype == "set-speaker":
            spk = (data or {}).get("speaker") if isinstance(data, dict) else None
            try:
                applied = omni.set_speaker(str(spk or ""))
                await rtvi.send_server_message({"type": "rfomni-speaker", "speaker": applied})
            except Exception as exc:
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})
        elif mtype in ("enroll-voice", "enroll"):
            # Arming is all this does; the voiceprint is taken from the user's next
            # spoken turn, so no audio has to be uploaded over the data channel.
            try:
                await rtvi.send_server_message(omni.arm_enroll())
            except Exception as exc:
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})
        elif mtype in ("set-auto-enroll", "set-auto"):
            # V5 only. Off falls back to V4 behaviour: nothing is captured until the
            # user presses "Register my voice".
            on = (data or {}).get("on") if isinstance(data, dict) else None
            try:
                await rtvi.send_server_message(omni.set_auto_enroll(bool(on)))
            except Exception as exc:
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})
        elif mtype in ("reset-voice", "reset-enroll"):
            try:
                await rtvi.send_server_message(await omni.reset_enroll())
            except Exception as exc:
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})
        elif mtype in ("set-diarize", "set-diarization"):
            on = (data or {}).get("on") if isinstance(data, dict) else None
            try:
                await rtvi.send_server_message(omni.set_diarize(bool(on)))
            except Exception as exc:
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})
                await rtvi.send_server_message(omni.gate_stats())
        elif mtype in ("set-barge-gate", "set-barge"):
            on = (data or {}).get("on") if isinstance(data, dict) else None
            try:
                await rtvi.send_server_message(await omni.set_barge_gate(bool(on)))
            except Exception as exc:
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})
        elif mtype == "set-gate":
            on = (data or {}).get("on") if isinstance(data, dict) else None
            try:
                await rtvi.send_server_message(omni.set_gate(bool(on)))
            except Exception as exc:
                # Most often "register a voice first" — surface it, keep the gate off.
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})
                await rtvi.send_server_message(omni.gate_stats())
        elif mtype in ("set-lang", "set-language", "set-force-lang"):
            lang_val = None
            if isinstance(data, dict):
                lang_val = data.get("lang") or data.get("language") or data.get("force_lang")
            try:
                applied = omni.set_force_lang(str(lang_val or ""))
                await rtvi.send_server_message(
                    {"type": "rfomni-lang", "lang": applied or "auto"}
                )
                await rtvi.send_server_message(omni.memory_stats())
            except Exception as exc:
                await rtvi.send_server_message({"type": "rfomni-error", "error": str(exc)})

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        sid = session_id[:8] or "?"
        logger.info(
            "WebRTC connected session={} avatar={} vision={} multi-user cap={}",
            sid,
            avatar_kind,
            omni._vision_enabled,
            max_concurrent_turns(),
        )
        try:
            await maybe_capture_participant_camera(transport, client, framerate=1)
            await worker.rtvi.send_server_message(
                {"type": "rfomni-vision", "enabled": True}
            )
        except Exception as exc:
            logger.warning("camera capture not available: {}", exc)
            await worker.rtvi.send_server_message(
                {"type": "rfomni-vision", "enabled": False}
            )
        await worker.rtvi.send_server_message(omni.memory_stats())
        await worker.rtvi.send_server_message(
            {"type": "rfomni-mode", "mode": omni._settings.mode}
        )
        await worker.rtvi.send_server_message(
            {"type": "rfomni-speaker", "speaker": omni._settings.speaker}
        )
        await worker.rtvi.send_server_message(
            {
                "type": "rfomni-lang",
                "lang": omni._settings.force_lang or "auto",
            }
        )
        # Probe the speaker embedder once; the client hides the gate controls entirely
        # when it is not reachable, so V2 degrades to V1 rather than showing dead buttons.
        try:
            ok = await omni.gate_probe()
            logger.info("speaker gate {}", "available" if ok else "unavailable")
        except Exception as exc:
            logger.warning("gate probe failed: {}", exc)
        await worker.rtvi.send_server_message(omni.gate_stats())
        if omni._settings.mode != "studio":  # the studio panel already greets in text
            await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("WebRTC client disconnected")
        await runner.cancel()

    logger.info(
        "Qwen3-Omni session {} ready — {} model={} avatar={}",
        session_id[:8] or "?",
        base_url,
        model,
        avatar_kind,
    )
    await runner.run()


async def bot(runner_args: RunnerArguments):
    webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection
    vw, vh = _video_size()
    # Bot Video track only when an avatar is enabled; otherwise leave the panel empty.
    avatar_on = (os.environ.get("OMNI_AVATAR") or "none").strip().lower() not in (
        "none",
        "off",
        "0",
        "false",
        "no",
        "empty",
        "",
    )
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_in_enabled=True,
            video_out_enabled=avatar_on,
            video_out_is_live=avatar_on,
            video_out_width=vw,
            video_out_height=vh,
            audio_in_filter=_optional_noise_filter(),
        ),
    )
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    _ssl_cert = os.environ.get("SSL_CERTFILE")
    _ssl_key = os.environ.get("SSL_KEYFILE")
    if _ssl_cert and _ssl_key:
        import uvicorn

        _uvicorn_run = uvicorn.run

        def _uvicorn_run_https(app, host, port, **kwargs):
            return _uvicorn_run(
                app,
                host=host,
                port=port,
                ssl_certfile=_ssl_cert,
                ssl_keyfile=_ssl_key,
                **kwargs,
            )

        uvicorn.run = _uvicorn_run_https

    from pipecat.runner.run import main

    main()
