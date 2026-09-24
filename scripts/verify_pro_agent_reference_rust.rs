// Compile this consumer against an extracted candidate crate, not the checkout.
use leanctx_sdk::{AgentContext, AgentPermissions, EngineProtocolError, ExecutionPolicy, ReadMode};
use serde_json::json;
use std::{collections::BTreeMap, error::Error, fs, path::PathBuf, time::Duration};

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
    let context = open(&args[0])?;
    let read = context
        .read("login.py", ReadMode::Full, false)?
        .text()
        .to_owned();
    let composed = context
        .compose("investigate authentication retry", ".")?
        .text()
        .to_owned();
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
    fs::write(
        &policy,
        format!("{rules}[context]\ndeny_tools=[\"ctx_read\"]\n"),
    )?;
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
    fs::write(&policy, rules)?;
    let restored = context
        .read("login.py", ReadMode::Full, false)?
        .text()
        .to_owned();
    checks.insert(
        "same_session_rule_repair",
        restored.contains("REFRESH_SESSION_FIRST") && !restored.contains("CUS-1234"),
    );
    responses.insert("restored", restored);
    context.close()?;
    let passed = checks.len() == 5 && checks.values().all(|value| *value);
    fs::write(
        &args[3],
        serde_json::to_vec_pretty(&json!({"passed":passed,"checks":checks,"responses":responses}))?,
    )?;
    if !passed {
        return Err("installed reference failed".into());
    }
    Ok(())
}
