#
# SecondLine — live voice agent (Pipecat Cloud entrypoint).
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""SecondLine: the self-improving voice agent that answers missed calls for a
local business (built on the YC Voice Agents Hackathon Field & Flower starter).

Pipeline: STT -> LLM (Nemotron-3-Super, GPT-4.1 fallback) -> Gradium TTS, with
direct function tools wired to the SecondLine business brain (backend.py via
agent.py). Caller identity comes from Twilio caller ID; everything the agent
learns is persisted so the *next* call is better.

Run locally::

    uv run bot.py            # default: Nemotron if NEMOTRON_LLM_URL set, else GPT

Pick a model explicitly::

    LLM_PROVIDER=gpt uv run bot.py
    LLM_PROVIDER=nemotron uv run bot.py
"""

import os
import time

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndTaskFrame, FunctionCallResultProperties, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.types import (
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

import agent as agent_mod
import backend
import policy as policy_mod

# Optional transcript logging (graceful if the processor isn't in this version).
try:
    from pipecat.processors.transcript_processor import TranscriptProcessor
    _HAS_TRANSCRIPT = True
except Exception:  # pragma: no cover
    _HAS_TRANSCRIPT = False

load_dotenv(override=True)

# Seed the business brain on cold start (idempotent — won't duplicate).
backend.seed()


async def get_call_info(call_sid: str) -> dict:
    """Fetch caller/called numbers from the Twilio REST API."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not account_sid or not auth_token:
        logger.warning("Missing Twilio credentials, cannot fetch call info")
        return {}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"
    try:
        auth = aiohttp.BasicAuth(account_sid, auth_token)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=auth) as response:
                if response.status != 200:
                    logger.error(f"Twilio API error ({response.status}): {await response.text()}")
                    return {}
                data = await response.json()
                return {"from_number": data.get("from"), "to_number": data.get("to")}
    except Exception as e:
        logger.error(f"Error fetching call info from Twilio: {e}")
        return {}


def _build_llm(system_instruction: str):
    """Select the LLM. Default Nemotron when its URL is configured, else GPT-4.1.
    Opt into Claude with LLM_PROVIDER=claude (uses your Anthropic credits).

    Returns (llm_service, model_label, system_in_context). GPT/Nemotron take the
    system prompt via Settings; Claude (Pipecat AnthropicLLMService) takes it via
    the context, so the caller injects it there when the flag is True.
    """
    provider = os.environ.get("LLM_PROVIDER", "").lower()
    nemotron_url = os.environ.get("NEMOTRON_LLM_URL", "")
    use_nemotron = provider == "nemotron" or (provider == "" and bool(nemotron_url))

    if provider == "claude":
        from pipecat.services.anthropic.llm import AnthropicLLMService
        # Default to the most capable model; set CLAUDE_MODEL=claude-haiku-4-5 or
        # claude-sonnet-4-6 for lower voice latency if needed.
        model = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
        llm = AnthropicLLMService(api_key=os.environ["ANTHROPIC_API_KEY"], model=model)
        logger.info(f"LLM: Anthropic Claude ({model})")
        return llm, f"claude:{model}", True

    if use_nemotron and nemotron_url:
        from nemotron_llm import VLLMOpenAILLMService
        model = os.environ.get("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")
        enable_thinking = os.getenv("NEMOTRON_ENABLE_THINKING", "false").lower() == "true"
        llm = VLLMOpenAILLMService(
            api_key=os.getenv("NEMOTRON_LLM_API_KEY", os.getenv("NEMOTRON_API_KEY", "EMPTY")),
            base_url=nemotron_url,
            settings=VLLMOpenAILLMService.Settings(
                model=model,
                system_instruction=system_instruction,
                extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}}},
            ),
        )
        logger.info(f"LLM: Nemotron ({model}) via {nemotron_url}")
        return llm, f"nemotron:{model}", False

    # GPT-4.1 fallback
    from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1")
    llm = OpenAIResponsesLLMService(
        api_key=os.environ["OPENAI_API_KEY"],
        settings=OpenAIResponsesLLMService.Settings(
            model=model, system_instruction=system_instruction
        ),
    )
    logger.info(f"LLM: OpenAI ({model})")
    return llm, f"gpt:{model}", False


def _build_stt():
    """Nemotron Speech Streaming STT when configured, else Gradium STT."""
    asr_url = os.environ.get("NVIDIA_ASR_URL", "")
    if asr_url:
        try:
            from nvidia_stt import NVidiaWebSocketSTTService
            logger.info(f"STT: NVIDIA Nemotron ASR via {asr_url}")
            return NVidiaWebSocketSTTService(url=asr_url, strip_interim_prefix=True)
        except Exception as e:
            logger.warning(f"NVIDIA ASR unavailable ({e}); falling back to Gradium STT")
    return GradiumSTTService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumSTTService.Settings(language=Language.EN),
    )


async def run_bot(
    transport: BaseTransport,
    from_number: str | None = None,
    call_id: str = "local",
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
):
    logger.info("Starting SecondLine bot")
    phone = from_number or "anonymous"

    # Build the per-call agent session + system prompt from persistent memory
    # FIRST, so we can hand the prompt to the LLM at construction time.
    session = agent_mod.AgentSession(
        phone=phone,
        call_id=call_id,
        owner_number=os.environ.get("OWNER_PHONE_NUMBER", ""),
        policy=policy_mod.load_policy(),
    )
    session.refresh_memory()
    system_instruction = agent_mod.build_system_prompt(session.memory, session.policy)

    llm, model_label, system_in_context = _build_llm(system_instruction)
    session.model = model_label
    backend.start_call(call_id, phone, model_label)
    stt = _build_stt()
    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(
            voice=os.getenv("GRADIUM_VOICE_ID", "_6Aslh2DxfmnRLmP")
        ),
    )

    first_response_ts: dict = {"t": None, "start": time.time()}

    # --- Wrap each shared tool as a Pipecat direct function -------------------
    def make_tool(tool_name: str):
        async def _tool(params: FunctionCallParams, **kwargs):
            result = await agent_mod.dispatch(session, tool_name, kwargs)
            if tool_name == "end_call":
                await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
                backend.end_call(call_id, "completed" if not session.escalated else "escalated",
                                 _first_ms(first_response_ts))
                await params.result_callback(
                    result, properties=FunctionCallResultProperties(run_llm=False)
                )
                return
            await params.result_callback(result)
        _tool.__name__ = tool_name
        _tool.__doc__ = next(t["description"] for t in agent_mod.TOOL_SCHEMAS if t["name"] == tool_name)
        return _tool

    tool_functions = [make_tool(t["name"]) for t in agent_mod.TOOL_SCHEMAS]
    tools = ToolsSchema(standard_tools=tool_functions)
    for fn in tool_functions:
        llm.register_direct_function(fn)

    # GPT/Nemotron get the system prompt via Settings (see _build_llm); Claude
    # takes it from the context.
    context = LLMContext(tools=tools)
    if system_in_context:
        context.add_message({"role": "system", "content": system_instruction})

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=FilterIncompleteUserTurnStrategies(),
        ),
    )

    pipeline_stages = [transport.input(), stt, user_aggregator, llm, tts,
                       transport.output(), assistant_aggregator]

    # Transcript logging (best-effort) for the owner dashboard + audit trail.
    if _HAS_TRANSCRIPT:
        transcript = TranscriptProcessor()

        @transcript.event_handler("on_transcript_update")
        async def on_transcript_update(proc, frame):
            for msg in frame.messages:
                if first_response_ts["t"] is None and getattr(msg, "role", "") == "assistant":
                    first_response_ts["t"] = time.time()
                try:
                    backend.log_turn(call_id, phone, getattr(msg, "role", "?"),
                                     getattr(msg, "content", "") or "")
                except Exception:
                    pass

        # insert transcript taps around the aggregators
        pipeline_stages = [transport.input(), stt, transcript.user(), user_aggregator,
                           llm, tts, transport.output(), assistant_aggregator, transcript.assistant()]

    pipeline = Pipeline(pipeline_stages)
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
    )

    greeting = ("This is Field & Flower, your local flower shop. How can I help you today?"
                if not session.memory else
                "Welcome back to Field & Flower! How can I help today?")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Caller connected: {phone} (model {model_label})")
        context.add_message({"role": "user",
                             "content": f"A customer just called. Greet them: '{greeting}'"})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Caller disconnected")
        backend.end_call(call_id, "completed" if not session.escalated else "escalated",
                         _first_ms(first_response_ts))
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


def _first_ms(d: dict) -> int | None:
    if d.get("t") is None:
        return None
    return int((d["t"] - d["start"]) * 1000)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point (SmallWebRTC for local, Twilio websocket for phone)."""
    from_number: str | None = None
    call_id = "local"
    transport_overrides: dict = {}

    if os.environ.get("ENV") != "local":
        from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter
        krisp_filter = KrispVivaFilter()
    else:
        krisp_filter = None

    match runner_args:
        case SmallWebRTCRunnerArguments():
            transport = SmallWebRTCTransport(
                webrtc_connection=runner_args.webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )
            call_id = f"webrtc-{int(time.time())}"
        case WebSocketRunnerArguments():
            transport_overrides["audio_in_sample_rate"] = 8000
            transport_overrides["audio_out_sample_rate"] = 8000
            _, call_data = await parse_telephony_websocket(runner_args.websocket)
            call_id = call_data["call_id"]
            call_info = await get_call_info(call_data["call_id"])
            if call_info:
                from_number = call_info.get("from_number")
                logger.info(f"Call from: {from_number} to: {call_info.get('to_number')}")
            serializer = TwilioFrameSerializer(
                stream_sid=call_data["stream_id"],
                call_sid=call_data["call_id"],
                account_sid=os.environ["TWILIO_ACCOUNT_SID"],
                auth_token=os.environ["TWILIO_AUTH_TOKEN"],
            )
            transport = FastAPIWebsocketTransport(
                websocket=runner_args.websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport, from_number=from_number, call_id=call_id, **transport_overrides)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
