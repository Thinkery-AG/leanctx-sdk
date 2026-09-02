"""Provider-free behavioral verification against real OpenAI Agents 0.8.4."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import tempfile
from pathlib import Path

from agents import (
    Agent,
    Model,
    ModelResponse,
    RunContextWrapper,
    Usage,
    set_tracing_disabled,
)
from leanctx_sdk import (
    AgentContext,
    ContextSession,
    ContextSource,
    SubprocessEngineClient,
)
from leanctx_sdk.integrations.openai_agents import OpenAIAgentsAdapter, openai_tools
from openai.types.responses import ResponseOutputMessage, ResponseOutputText


class _SuccessModel(Model):
    async def get_response(self, *args, **kwargs):
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="provider-free-message",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            logprobs=[],
                            text="provider-free-ok",
                            type="output_text",
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ],
            usage=Usage(),
            response_id="provider-free-response",
        )

    def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is outside this gate")


class _FailingModel(Model):
    def __init__(self, error):
        self.error = error

    async def get_response(self, *args, **kwargs):
        raise self.error

    def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is outside this gate")


def _session(engine: Path, root: Path) -> ContextSession:
    session = ContextSession(
        "provider-free Agents gate",
        project_root=str(root),
        engine=SubprocessEngineClient(engine),
    )
    session.plan(ContextSource("context.txt", project_root=str(root)))
    return session


def verify(engine: Path) -> dict[str, object]:
    set_tracing_disabled(True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "context.txt").write_text("certified context\n", encoding="utf-8")

        success = OpenAIAgentsAdapter(
            Agent(name="provider-free-success", model=_SuccessModel()),
            _session(engine, root),
        )
        result = success.run_sync("caller-input", outcome="completed")
        if result.final_output != "provider-free-ok":
            raise AssertionError("real Runner result was not preserved")
        if success.receipt is None or not success.receipt.verify():
            raise AssertionError("success receipt did not verify")

        error = RuntimeError("provider-owned secret must not serialize")
        failure = OpenAIAgentsAdapter(
            Agent(name="provider-free-failure", model=_FailingModel(error)),
            _session(engine, root),
        )
        try:
            failure.run_sync("caller-input")
        except RuntimeError as caught:
            if caught is not error:
                raise AssertionError(
                    "real Runner exception identity changed"
                ) from caught
        else:
            raise AssertionError("real Runner failure did not propagate")
        if failure.receipt is None or failure.receipt.exception is not error:
            raise AssertionError("abort receipt did not preserve host exception")
        serialized = json.dumps(dict(failure.receipt.to_dict()), sort_keys=True)
        if "provider-owned secret" in serialized:
            raise AssertionError("abort receipt serialized exception text")

        (root / "agent-tools.txt").write_text(
            "AGENT_TOOLS_RUNTIME_FACT=verified\n", encoding="utf-8"
        )
        with AgentContext(
            root,
            task="Find AGENT_TOOLS_RUNTIME_FACT",
            engine_binary=engine,
        ) as context:
            tools = {tool.name: tool for tool in openai_tools(context)}
            required = {"leanctx_read", "leanctx_search", "leanctx_tree"}
            if not required.issubset(tools):
                raise AssertionError("OpenAI function-tool surface is incomplete")
            wrapper = RunContextWrapper(context=None)
            search_result = asyncio.run(
                tools["leanctx_search"].on_invoke_tool(
                    wrapper,
                    json.dumps(
                        {
                            "pattern": "AGENT_TOOLS_RUNTIME_FACT",
                            "path": ".",
                            "max_results": 20,
                        },
                        sort_keys=True,
                    ),
                )
            )
            if "AGENT_TOOLS_RUNTIME_FACT" not in str(search_result):
                raise AssertionError("real OpenAI function tool lost Engine context")
            read_result = asyncio.run(
                tools["leanctx_read"].on_invoke_tool(
                    wrapper,
                    json.dumps(
                        {"path": "agent-tools.txt", "mode": "full"},
                        sort_keys=True,
                    ),
                )
            )
            if "AGENT_TOOLS_RUNTIME_FACT" not in str(read_result):
                raise AssertionError("real OpenAI read tool lost Engine context")
            tree_result = asyncio.run(
                tools["leanctx_tree"].on_invoke_tool(
                    wrapper,
                    json.dumps({"path": ".", "depth": 2}, sort_keys=True),
                )
            )
            if "agent-tools.txt" not in str(tree_result):
                raise AssertionError("real OpenAI tree tool lost Engine context")

        sdk_path = Path(inspect.getfile(ContextSession)).resolve()
        if "site-packages" not in sdk_path.parts:
            raise AssertionError("verification imported SDK from source tree")
        return {
            "abort_receipt_verified": failure.receipt.verify(),
            "real_agents_runner": True,
            "real_agent_tools": sorted(required),
            "sdk_import": str(sdk_path),
            "status": "PASS",
            "success_receipt_verified": success.receipt.verify(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.engine.resolve(strict=True)), sort_keys=True))


if __name__ == "__main__":
    main()
