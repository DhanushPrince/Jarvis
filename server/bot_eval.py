"""Text-first Pipecat eval entrypoint for Jarvis."""

import argparse
import os

from pipecat.evals.transport import EvalTransportParams
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from config import CONFIG


class SmokeLLM(FrameProcessor):
    """Deterministic, dependency-free responder for the transport smoke test."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        messages = frame.context.get_messages()
        question = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        answer = (
            "The capital of Germany is Berlin."
            if "capital of germany" in question.lower()
            else "I am Jarvis. How can I help?"
        )
        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(LLMTextFrame(text=answer))
        await self.push_frame(LLMFullResponseEndFrame())


def make_llm(mode: str):
    if mode == "smoke":
        return SmokeLLM()
    return OpenAILLMService(
        api_key=CONFIG["llm"]["api_key"],
        base_url=CONFIG["llm"]["base_url"],
        settings=OpenAILLMService.Settings(
            model=CONFIG["llm"]["model"],
            max_tokens=CONFIG["llm"]["max_tokens"],
            system_instruction=CONFIG["llm"]["system_prompt"],
        ),
    )


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    context = LLMContext(
        messages=[{"role": "system", "content": CONFIG["llm"]["system_prompt"]}]
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)
    pipeline = Pipeline(
        [
            transport.input(),
            user_aggregator,
            make_llm(runner_args.cli_args.llm),
            transport.output(),
            assistant_aggregator,
        ]
    )
    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(
        runner_args,
        {
            "eval": lambda: EvalTransportParams(
                audio_in_enabled=False,
                audio_out_enabled=False,
            )
        },
    )
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jarvis Pipecat eval bot")
    parser.add_argument(
        "--llm",
        choices=("smoke", "openai"),
        default=os.getenv("JARVIS_EVAL_LLM", "smoke"),
        help="smoke is deterministic; openai uses config.yaml's local-compatible endpoint",
    )
    from pipecat.runner.run import main

    main(parser)
