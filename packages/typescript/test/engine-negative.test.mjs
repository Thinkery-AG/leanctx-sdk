import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  ContextPlan,
  ContextSource,
  EngineUnavailable,
  SourceUnavailableError,
  SubprocessEngineClient,
} from "../dist/index.js";

function failingEngine(root, code) {
  const path = join(root, "failing-engine");
  writeFileSync(path, `#!${process.execPath}\nprocess.stderr.write("engine: ${code}\\n"); process.exit(2);\n`, { mode: 0o700 });
  chmodSync(path, 0o700);
  return path;
}

test("nonzero Engine exits preserve typed failure mapping without stderr leakage", async () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-engine-failure-"));
  try {
    writeFileSync(join(root, "source.txt"), "source\n");
    const source = new ContextSource("source.txt", { projectRoot: root });
    const plan = new ContextPlan("session", "task", "inspect", source);
    const engine = new SubprocessEngineClient({ engineBinary: failingEngine(root, "source_unavailable") });
    await assert.rejects(engine.contextView(plan), (error) => {
      assert.ok(error instanceof SourceUnavailableError);
      assert.equal(error.message, "Engine source is unavailable");
      return true;
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("an empty PATH entry cannot select a cwd-local Engine", async () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-engine-path-"));
  const previousCwd = process.cwd();
  const previousPath = process.env.PATH;
  try {
    writeFileSync(join(root, "source.txt"), "source\n");
    writeFileSync(join(root, "lean-ctx"), `#!${process.execPath}\nprocess.exit(0);\n`, { mode: 0o700 });
    chmodSync(join(root, "lean-ctx"), 0o700);
    process.chdir(root);
    process.env.PATH = "";
    const source = new ContextSource("source.txt", { projectRoot: root });
    const plan = new ContextPlan("session", "task", "inspect", source);
    await assert.rejects(new SubprocessEngineClient().contextView(plan), EngineUnavailable);
  } finally {
    process.chdir(previousCwd);
    if (previousPath === undefined) delete process.env.PATH;
    else process.env.PATH = previousPath;
    rmSync(root, { recursive: true, force: true });
  }
});
