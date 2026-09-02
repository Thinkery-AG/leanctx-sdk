"""Provider-free Preview workspace/checkpoint/fork/delta/handoff smoke example."""

from __future__ import annotations

import tempfile

from leanctx_sdk.preview import ContextWorkspace, ProjectContextEntry


def main() -> None:
    with tempfile.TemporaryDirectory() as state_root:
        workspace = ContextWorkspace.create(
            state_root,
            "source",
            workspace_id="00000000-0000-4000-8000-000000000001",
        )
        entry_id = "00000000-0000-4000-8000-000000000010"
        workspace.commit_context(
            [ProjectContextEntry(entry_id, "facts", "provider-free example")]
        )
        base = workspace.checkpoint(
            checkpoint_id="00000000-0000-4000-8000-000000000002",
            event_id="00000000-0000-4000-8000-000000000003",
        )
        reopened = ContextWorkspace.open(state_root, workspace.workspace_id)
        transient_entry_id = "00000000-0000-4000-8000-000000000011"
        reopened.commit_context(
            [ProjectContextEntry(transient_entry_id, "facts", "restore removes this")]
        )
        reopened.restore(
            base,
            event_id="00000000-0000-4000-8000-000000000012",
        )
        assert tuple(
            entry.entry_id for entry in reopened.project_context().entries
        ) == (entry_id,)
        child = reopened.fork(
            "child",
            from_checkpoint=base,
            workspace_id="00000000-0000-4000-8000-000000000004",
            fork_id="00000000-0000-4000-8000-000000000005",
            event_id="00000000-0000-4000-8000-000000000006",
        )
        target = child.checkpoint(
            checkpoint_id="00000000-0000-4000-8000-000000000007",
            event_id="00000000-0000-4000-8000-000000000008",
        )
        delta = child.context_delta(base, target)
        handoff = reopened.create_handoff(
            base,
            target_workspace_id=child.workspace_id,
            task="continue locally",
            entry_ids=(entry_id,),
            handoff_id="00000000-0000-4000-8000-000000000009",
        )
        admission = child.admit_handoff(handoff, target)
        assert delta.base.checkpoint_id == base.checkpoint_id
        assert handoff.target_workspace_id == child.workspace_id
        assert admission.decision == "admitted"


if __name__ == "__main__":
    main()
