import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Dict

# Add local pipecat to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipecat", "src"))

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.openai.llm import OpenAILLMService

from pipecat.services.whisper.stt import WhisperSTTServiceMLX, MLXModel
from pipecat.transports.base_transport import TransportParams
from pipecat.processors.frameworks.rtvi.observer import RTVIObserver
from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_start import (
    VADUserTurnStartStrategy,
    TranscriptionUserTurnStartStrategy,
)

from tts_mlx_isolated import TTSMLXIsolated

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# 8GB-safe configuration (LAP0014, Apple M4, 8GB unified memory)
# All knobs are env-overridable so you can swap models without editing code.
#   STT_ENGINE      : "whisper" (default, proven) | "parakeet"
#   WHISPER_MODEL   : HF repo string for MLX whisper (default base = ~0.5GB RSS)
#   PARAKEET_MODEL  : HF repo for parakeet-mlx (INT8 recommended, ~1.27GB peak)
#   LLM_MODEL       : model name your local :1234 server exposes
#   LLM_BASE_URL    : OpenAI-compatible base url (local, no cloud)
# ---------------------------------------------------------------------------
# All settings now come from config.yaml (env vars still override). See config.py.
from config import CONFIG

STT_ENGINE = CONFIG["stt"]["engine"]
WHISPER_MODEL = CONFIG["stt"]["whisper_model"]
PARAKEET_MODEL = CONFIG["stt"]["parakeet_model"]
LLM_MODEL = CONFIG["llm"]["model"]
LLM_BASE_URL = CONFIG["llm"]["base_url"]

app = FastAPI()

pcs_map: Dict[str, SmallWebRTCConnection] = {}

ice_servers = [
    IceServer(
        urls="stun:stun.l.google.com:19302",
    )
]


SYSTEM_INSTRUCTION = CONFIG["llm"]["system_prompt"]


async def run_bot(webrtc_connection):
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # NOTE: In pipecat 1.0+ (this is 1.8.1) VAD and turn detection are NO
            # LONGER configured on TransportParams — those kwargs are silently
            # ignored here (TransportParams has no vad_analyzer/turn_analyzer
            # fields). They are configured on LLMUserAggregatorParams below.
        ),
    )

    # --- STT: 8GB-safe. Default Whisper-base (proven ~0.5GB RSS on LAP0014). ---
    # LARGE_V3_TURBO_Q4 (repo default) is too heavy to sit co-resident with the
    # LLM + Kokoro on 8GB, so we default to base and expose Parakeet as an opt-in.
    if STT_ENGINE == "parakeet":
        # Parakeet-MLX path. Requires: pip install parakeet-mlx
        # Uses a thin Pipecat SegmentedSTTService wrapper defined in
        # parakeet_stt.py (see SMOKE_REPORT.md "Parakeet next patch").
        from parakeet_stt import ParakeetMLXSTTService

        stt = ParakeetMLXSTTService(model=PARAKEET_MODEL)
    else:
        # MLXModel enum has no BASE/SMALL, but the service accepts any repo string.
        stt = WhisperSTTServiceMLX(model=WHISPER_MODEL)

    tts = TTSMLXIsolated(
        model=CONFIG["tts"]["model"],
        voice=CONFIG["tts"]["voice"],
        sample_rate=CONFIG["tts"]["sample_rate"],
    )

    llm = OpenAILLMService(
        api_key=CONFIG["llm"]["api_key"],
        model=LLM_MODEL,  # name = whatever the local :1234 server exposes
        base_url=LLM_BASE_URL,
        max_tokens=CONFIG["llm"]["max_tokens"],
    )

    context = LLMContext(
        messages=[
            {
                "role": "user",
                "content": SYSTEM_INSTRUCTION,
            }
        ],
    )
    # VAD + turn detection live on the USER AGGREGATOR in pipecat 1.0+ (1.8.1).
    # This is the fix for "bot speaks but never listens": with the analyzers on
    # TransportParams (pre-1.0 location) they were silently ignored, so VAD
    # never fired UserStartedSpeaking and STT never ran.
    _vad = CONFIG["vad"]
    _stop_strategy = TurnAnalyzerUserTurnStopStrategy(
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    )
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    confidence=float(_vad["confidence"]),
                    start_secs=float(_vad["start_secs"]),
                    stop_secs=float(_vad["stop_secs"]),
                    min_volume=float(_vad["min_volume"]),
                )
            ),
            # smart_turn=true -> AI end-of-turn model; false -> VAD stop_secs only
            user_turn_strategies=UserTurnStrategies(
                start=[
                    VADUserTurnStartStrategy(),
                    TranscriptionUserTurnStartStrategy(),
                ],
                stop=[_stop_strategy] if CONFIG["turn"]["smart_turn"] else [],
            ),
        ),
    )

    #
    # RTVI events for Pipecat client UI
    #
    rtvi = RTVIProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            rtvi,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
    )

    @rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        await rtvi.set_bot_ready()
        # Kick off the conversation (pipecat 1.8.1: run the LLM on current context)
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        # SmallWebRTC fires on_client_connected (NOT on_first_participant_joined,
        # which is a Daily-only event). Do NOT call capture_participant_audio()
        # here: in pipecat 1.8.1 the input transport auto-starts audio reception
        # in start() (audio_in_enabled=True), which is what creates the
        # _audio_in_queue. Calling capture here can start _receive_audio() BEFORE
        # the queue exists, causing:
        #   AttributeError: 'SmallWebRTCInputTransport' object has no attribute
        #   '_audio_in_queue'
        # which kills the audio-receive task -> no audio to VAD/STT.
        print(f"Client connected: {client}")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        print(f"Client disconnected: {client}")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)

    await runner.run(task)


@app.post("/api/offer")
async def offer(request: dict, background_tasks: BackgroundTasks):
    pc_id = request.get("pc_id")

    if pc_id and pc_id in pcs_map:
        pipecat_connection = pcs_map[pc_id]
        logger.info(f"Reusing existing connection for pc_id: {pc_id}")
        await pipecat_connection.renegotiate(
            sdp=request["sdp"],
            type=request["type"],
            restart_pc=request.get("restart_pc", False),
        )
    else:
        pipecat_connection = SmallWebRTCConnection(ice_servers)
        await pipecat_connection.initialize(sdp=request["sdp"], type=request["type"])

        @pipecat_connection.event_handler("closed")
        async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
            logger.info(f"Discarding peer connection for pc_id: {webrtc_connection.pc_id}")
            pcs_map.pop(webrtc_connection.pc_id, None)

        # Run example function with SmallWebRTC transport arguments.
        background_tasks.add_task(run_bot, pipecat_connection)

    answer = pipecat_connection.get_answer()
    # Updating the peer connection inside the map
    pcs_map[answer["pc_id"]] = pipecat_connection

    return answer


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # Run app
    coros = [pc.disconnect() for pc in pcs_map.values()]
    await asyncio.gather(*coros)
    pcs_map.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipecat Bot Runner")
    parser.add_argument(
        "--host", default="localhost", help="Host for HTTP server (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=7860, help="Port for HTTP server (default: 7860)"
    )
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
