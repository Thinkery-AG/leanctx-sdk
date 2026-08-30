"""Minimal framework-neutral agent tool session."""

from leanctx_sdk import AgentContext


def main() -> None:
    with AgentContext(".", task="Explain the SDK public API") as tools:
        context = "\n".join(
            (
                tools.tree(depth=2).text,
                tools.search("AgentContext", path="src").text,
                tools.read("src/leanctx_sdk/__init__.py", mode="signatures").text,
            )
        )
        # Send `tools.task` and `context` to the model or framework you own.
        print(context)
        print("saved tokens:", tools.metrics.saved_tokens)


if __name__ == "__main__":
    main()
