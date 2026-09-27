// Copy into a fresh npm consumer before running; no source-relative SDK imports.
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { AgentContext, AgentPermissionError, EngineProtocolError } from "@thinkery/leanctx-sdk";

// Optional --project PATH consumes a caller-prepared project without changing its initial files.
const argv = process.argv.slice(2);
let projectRoot;
const projectFlagIndex = argv.indexOf("--project");
if (projectFlagIndex >= 0) {
  if (!argv[projectFlagIndex + 1]) throw new Error("--project requires a path");
  projectRoot = resolve(argv[projectFlagIndex + 1]);
  argv.splice(projectFlagIndex, 2);
}
const [engine, previousEngine, output] = argv;
if (argv.length !== 3) throw new Error("engine, previous engine and output are required; optional --project PATH");
const installed = fileURLToPath(import.meta.resolve("@thinkery/leanctx-sdk"));
if (!installed.startsWith(resolve("node_modules") + "/")) throw new Error("SDK is not installed in this fresh consumer");
const temporaryRoot = projectRoot ? undefined : await mkdtemp(join(tmpdir(), "leanctx-pro-typescript-reference-"));
const root = projectRoot ?? temporaryRoot;
const checks = {};
const responses = {};
const fixtureRules = 'name="installed-reference"\nversion="1.0.0"\ndescription="test"\n[filters]\nclassification="block"\n[redaction]\ncustomer="CUS-[0-9]{4}"\n';
const policy = join(root, ".lean-ctx/policy.toml");
const sourceConfig = process.env.LEANCTX_REFERENCE_GITLAB_SOURCE
  ? JSON.parse(await readFile(process.env.LEANCTX_REFERENCE_GITLAB_SOURCE, "utf8")) : undefined;
try {
  if (!projectRoot) {
    await mkdir(join(root, ".git"));
    await mkdir(join(root, ".lean-ctx"));
    await writeFile(join(root, "login.py"), '# authentication retry: refresh the expired session before retrying\ndef authenticate():\n    return "REFRESH_SESSION_FIRST CUS-1234"\n');
    await writeFile(join(root, "private.py"), '# CONFIDENTIAL\ndef authentication_secret():\n    return "PRIVATE_CANARY"\n');
    await writeFile(policy, fixtureRules);
  }
  const rules = await readFile(policy, "utf8");
  try {
    const old = await AgentContext.open(root, { engineBinary: previousEngine });
    await old.close();
    checks.old_engine_rejected = false;
  } catch (error) {
    checks.old_engine_rejected = error instanceof EngineProtocolError && error.message.includes("hello is incompatible");
  }
  const {config_dir: configDir, ...sourceFields} = sourceConfig ?? {};
  const context = await AgentContext.open(root, { engineBinary: engine,
    gitlabSource: sourceConfig ? {...sourceFields, ...(configDir ? {configDir} : {})} : undefined });
  try {
    responses.read = (await context.read("login.py", "full")).text;
    responses.compose = (await context.compose("investigate authentication retry")).text;
    if (process.env.LEANCTX_REFERENCE_PRO === "1") {
      checks.pro_context_selection = responses.compose.includes("Pro context selection:")
        && !responses.compose.includes("Pro context selection unavailable");
    }
    checks.useful_masked_read = responses.read.includes("REFRESH_SESSION_FIRST") && responses.read.includes("REDACTED") && !responses.read.includes("CUS-1234");
    checks.useful_protected_compose = responses.compose.includes("REFRESH_SESSION_FIRST") && responses.compose.includes("login.py") && ["CUS-1234", "private.py", "PRIVATE_CANARY"].every(value => !responses.compose.includes(value));
    const temporaryRules = rules + (rules.endsWith("\n") ? "" : "\n");
    const deniedContext = '[context]\ndeny_tools=["ctx_read"]\n';
    await writeFile(policy, temporaryRules.includes('[context]') ? temporaryRules.replace('[context]', deniedContext) : temporaryRules + deniedContext);
    try {
      responses.denied = (await context.read("login.py", "full")).text;
      checks.changed_rule_blocks_read = responses.denied.includes("POLICY BLOCKED") && !responses.denied.includes("REFRESH_SESSION_FIRST") && !responses.denied.includes("CUS-1234");
    } catch (error) {
      responses.denied = String(error);
      checks.changed_rule_blocks_read = responses.denied.toLowerCase().includes("policy") && !responses.denied.includes("REFRESH_SESSION_FIRST") && !responses.denied.includes("CUS-1234");
    }
    await writeFile(policy, rules);
    responses.restored = (await context.read("login.py", "full")).text;
    checks.same_session_rule_repair = responses.restored.includes("REFRESH_SESSION_FIRST") && !responses.restored.includes("CUS-1234");
    if (sourceConfig) {
      const query = { action: "query", provider: "gitlab", resource: "merge_requests", project: String(sourceConfig.project), mode: "snapshot", limit: 1 };
      const snapshot = await context.call("ctx_provider", query);
      const observed = spawnSync(process.env.LEANCTX_REFERENCE_PYTHON, [process.env.LEANCTX_REFERENCE_SNAPSHOT_OBSERVER],
        { input: snapshot.text, encoding: "utf8", timeout: 15000, maxBuffer: 65536 });
      checks.live_selected_gitlab = observed.status === 0;
      for (const [name, change] of [["foreign_project_refused", {project: "other/project"}], ["unsupported_source_action_refused", {action: "refresh"}]]) {
        try { await context.call("ctx_provider", {...query, ...change}); checks[name] = false; }
        catch (error) { checks[name] = error instanceof AgentPermissionError; }
      }
      try {
        await rm(policy);
        try { await context.read("login.py", "full"); checks.source_policy_removal_closes_session = false; }
        catch (error) { checks.source_policy_removal_closes_session = error instanceof AgentPermissionError; }
      } finally { await writeFile(policy, rules); }
      for (const key of Object.keys(responses)) delete responses[key];
    }
  } finally {
    await context.close();
  }
} finally {
  if (temporaryRoot) await rm(temporaryRoot, { recursive: true, force: true });
}
const digest = async path => createHash("sha256").update(await readFile(path)).digest("hex");
const passed = Object.keys(checks).length >= 5 && Object.values(checks).every(Boolean);
await writeFile(output, JSON.stringify({ passed, checks, responses, installed_module: installed, engine_sha256: await digest(engine), previous_engine_sha256: await digest(previousEngine), scope: "Installed npm artifact, actual local Engine; optional selected GitLab only when configured, application output without Codex/model transmission." }, null, 2) + "\n");
console.log(JSON.stringify({ passed, checks }));
process.exitCode = passed ? 0 : 1;
