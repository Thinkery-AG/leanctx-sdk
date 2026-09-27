// Compile this consumer against an extracted candidate crate, not the checkout.
use leanctx_sdk::{AgentContext, AgentPermissions, EngineProtocolError, ExecutionPolicy, ReadMode, GitLabSource};
use serde_json::json;
use std::{collections::BTreeMap, error::Error, fs, path::PathBuf, time::Duration};
use std::{io::Write, process::{Command, Stdio}};

fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 4 {
        return Err("engine, previous engine, fixture root, output required".into());
    }
    let root = PathBuf::from(&args[2]);
    let policy = root.join(".lean-ctx/policy.toml");
    let rules = fs::read_to_string(&policy)?;
    let open = |binary: &str| {
        AgentContext::open_with_policy(
            &root,
            "",
            AgentPermissions::default(),
            ExecutionPolicy::default(),
            Some(binary.into()),
            Duration::from_secs(30),
        )
    };
    let mut checks = BTreeMap::new();
    let mut responses = BTreeMap::new();
    let old_rejected = match open(&args[1]) {
        Ok(old) => {
            old.close()?;
            false
        }
        Err(error) => {
            error.is::<EngineProtocolError>() && error.to_string().contains("hello is incompatible")
        }
    };
    checks.insert("old_engine_rejected", old_rejected);
    let source_config: Option<serde_json::Value> = std::env::var("LEANCTX_REFERENCE_GITLAB_SOURCE")
        .ok().map(|path| -> Result<_, Box<dyn Error + Send + Sync>> {
            Ok(serde_json::from_slice(&fs::read(path)?)?)
        }).transpose()?;
    let source = source_config.as_ref().map(|value| -> Result<_, Box<dyn Error + Send + Sync>> {
        Ok(GitLabSource::new(value["host"].as_str().ok_or("host missing")?,
            value["project"].as_u64().ok_or("project missing")?,
            value["namespace"].as_str().ok_or("namespace missing")?,
            value["glab"].as_str().ok_or("glab missing")?,
            value["config_dir"].as_str().map(PathBuf::from))?)
    }).transpose()?;
    let context = AgentContext::open_with_policy_and_gitlab_source(
        &root, "", AgentPermissions::default(), ExecutionPolicy::default(),
        Some(args[0].clone().into()), Duration::from_secs(30), source,
    )?;
    let read = context
        .read("login.py", ReadMode::Full, false)?
        .text()
        .to_owned();
    let composed = context
        .compose("investigate authentication retry", ".")?
        .text()
        .to_owned();
    if std::env::var("LEANCTX_REFERENCE_PRO").ok().as_deref() == Some("1") {
        checks.insert(
            "pro_context_selection",
            composed.contains("Pro context selection:")
                && !composed.contains("Pro context selection unavailable"),
        );
    }
    checks.insert(
        "useful_masked_read",
        read.contains("REFRESH_SESSION_FIRST")
            && read.contains("REDACTED")
            && !read.contains("CUS-1234"),
    );
    checks.insert(
        "useful_protected_compose",
        composed.contains("REFRESH_SESSION_FIRST")
            && composed.contains("login.py")
            && ["CUS-1234", "PRIVATE_CANARY", "private.py"]
                .iter()
                .all(|value| !composed.contains(value)),
    );
    responses.insert("read", read);
    responses.insert("compose", composed);
    let separator = if rules.ends_with('\n') { "" } else { "\n" };
    let denied_context = "[context]\ndeny_tools=[\"ctx_read\"]\n";
    let temporary_rules = if rules.contains("[context]") {
        rules.replacen("[context]", denied_context, 1)
    } else {
        format!("{rules}{separator}{denied_context}")
    };
    fs::write(&policy, temporary_rules)?;
    let (denied, blocked) = match context.read("login.py", ReadMode::Full, false) {
        Ok(result) => (
            result.text().to_owned(),
            result.text().contains("POLICY BLOCKED"),
        ),
        Err(error) => {
            let policy_error = error.is::<leanctx_sdk::AgentPermissionError>()
                || error.is::<leanctx_sdk::EngineExecutionError>();
            let message = error.to_string();
            let blocked = policy_error && message.to_lowercase().contains("policy");
            (message, blocked)
        }
    };
    checks.insert(
        "changed_rule_blocks_read",
        blocked && !denied.contains("REFRESH_SESSION_FIRST") && !denied.contains("CUS-1234"),
    );
    responses.insert("denied", denied);
    fs::write(&policy, &rules)?;
    let restored = context
        .read("login.py", ReadMode::Full, false)?
        .text()
        .to_owned();
    checks.insert(
        "same_session_rule_repair",
        restored.contains("REFRESH_SESSION_FIRST") && !restored.contains("CUS-1234"),
    );
    responses.insert("restored", restored);
    if let Some(source) = source_config {
        let query = json!({"action":"query","provider":"gitlab","resource":"merge_requests",
            "project":source["project"].as_u64().ok_or("project missing")?.to_string(),"mode":"snapshot","limit":1});
        let snapshot = context.call("ctx_provider", query.clone())?;
        let mut observer = Command::new(std::env::var("LEANCTX_REFERENCE_PYTHON")?)
            .arg(std::env::var("LEANCTX_REFERENCE_SNAPSHOT_OBSERVER")?)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()?;
        observer.stdin.take().ok_or("observer stdin unavailable")?.write_all(snapshot.text().as_bytes())?;
        checks.insert("live_selected_gitlab", observer.wait_with_output()?.status.success());
        for (name, key, value) in [("foreign_project_refused", "project", "other/project"),
            ("unsupported_source_action_refused", "action", "refresh")] {
            let mut denied = query.clone(); denied[key] = json!(value);
            checks.insert(name, context.call("ctx_provider", denied)
                .err().is_some_and(|error| error.is::<leanctx_sdk::AgentPermissionError>()));
        }
        fs::remove_file(&policy)?;
        let blocked = context.read("login.py", ReadMode::Full, false)
            .err().is_some_and(|error| error.is::<leanctx_sdk::AgentPermissionError>());
        fs::write(&policy, &rules)?;
        checks.insert("source_policy_removal_closes_session", blocked);
        responses.clear();
    }
    context.close()?;
    let passed = checks.len() >= 5 && checks.values().all(|value| *value);
    fs::write(
        &args[3],
        serde_json::to_vec_pretty(&json!({"passed":passed,"checks":checks,"responses":responses}))?,
    )?;
    if !passed {
        return Err("installed reference failed".into());
    }
    Ok(())
}
