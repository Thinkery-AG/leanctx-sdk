"""Host-owned Native Embed flow with an explicit Engine binary."""

from typing import Callable, Tuple

from leanctx_sdk import ContextReceipt, ContextSession, ContextSource, SubprocessEngineClient
from leanctx_sdk.integrations.native_embed import abort, complete, prepare


def run(
    host: Callable[[str], object],
    project_root: str,
    path: str,
    engine_binary: str,
) -> Tuple[object, ContextReceipt]:
    session = ContextSession(
        "example task",
        project_root=project_root,
        engine=SubprocessEngineClient(engine_binary),
    )
    view = prepare(session, ContextSource(path, project_root=project_root))
    prompt = view.text if view is not None else "use the host's original input"
    try:
        result = host(prompt)
    except BaseException as error:
        abort(session, error)
        raise
    return result, complete(session, result, outcome="completed")
