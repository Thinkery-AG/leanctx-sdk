// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
//! Context Store preview conformance (fixtures shared by all six SDKs) and live Engine.

use std::fs;
use std::path::PathBuf;

use leanctx_sdk::preview::{parse_policy_evidence, parse_task_lineage, ContextStoreScope};
use leanctx_sdk::{EngineProtocolError, SubprocessEngineClient};
use serde_json::Value;

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/context-store-preview-v1")
}

fn load(kind: &str, validity: &str, name: &str) -> Value {
    let raw = fs::read(
        fixtures()
            .join(kind)
            .join(validity)
            .join(format!("{name}.json")),
    )
    .expect("fixture");
    serde_json::from_slice(&raw).expect("fixture JSON")
}

fn names(kind: &str, validity: &str) -> Vec<String> {
    let manifest: Value =
        serde_json::from_slice(&fs::read(fixtures().join("manifest.json")).expect("manifest"))
            .expect("manifest JSON");
    manifest["documents"][kind][validity]
        .as_array()
        .expect("names")
        .iter()
        .map(|name| name.as_str().expect("name").to_owned())
        .collect()
}

fn parse(kind: &str, value: &Value) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    match kind {
        "evidence" => parse_policy_evidence(value).map(|_| ()),
        _ => parse_task_lineage(value).map(|_| ()),
    }
}

#[test]
fn every_valid_document_parses_and_every_invalid_one_is_rejected() {
    for kind in ["evidence", "lineage"] {
        for name in names(kind, "valid") {
            parse(kind, &load(kind, "valid", &name))
                .unwrap_or_else(|e| panic!("{kind}/{name}: {e}"));
        }
        for name in names(kind, "invalid") {
            let error = parse(kind, &load(kind, "invalid", &name))
                .expect_err(&format!("{kind}/{name} must be rejected"));
            assert!(
                error.downcast_ref::<EngineProtocolError>().is_some(),
                "{kind}/{name}"
            );
        }
    }
}

#[test]
fn unmeasured_never_reads_as_measured() {
    let evidence = parse_policy_evidence(&load("evidence", "valid", "rich")).expect("evidence");
    let unmeasured = &evidence.records[1];
    assert!(!unmeasured.quality.measured && unmeasured.quality.retained.is_none());
    assert!(unmeasured.security.regressions.is_none());
    assert_eq!(evidence.records[0].security.regressions, Some(0));
    let gapped = parse_task_lineage(&load("lineage", "valid", "gapped")).expect("lineage");
    assert!(!gapped.is_complete() && gapped.deliveries[0].summary.is_none());
}

#[test]
fn live_engine_fresh_scope_and_unknown_task() {
    let Ok(binary) = std::env::var("LEANCTX_ENGINE_BINARY") else {
        return;
    };
    let client = SubprocessEngineClient::with_binary(binary).expect("client");
    let temporary = std::env::temp_dir().join(format!("leanctx-store-{}", std::process::id()));
    fs::create_dir_all(&temporary).expect("root");
    // Private Engine storage refuses symlinked ancestors (macOS /var).
    let root = fs::canonicalize(&temporary).expect("resolved root");
    let scope = ContextStoreScope {
        project_id: Some("sdk-preview-fresh".to_owned()),
        tenant_id: None,
    };
    let evidence = client
        .read_policy_evidence(&root, &scope)
        .expect("evidence");
    assert!(evidence.records.is_empty() && evidence.evaluations.is_empty());
    let tenant = ContextStoreScope {
        tenant_id: Some("tenant-a".to_owned()),
        ..scope
    };
    let lineage = client
        .read_task_lineage(&root, "sdk-preview-unknown-task", &tenant)
        .expect("lineage");
    assert_eq!(lineage.outcome, "unknown");
    assert!(lineage.gaps.iter().any(|gap| gap == "no_plan_recorded"));
    assert!(lineage.scope.unwrap_or_default().contains("tenant-a"));
    let _ = fs::remove_dir_all(&temporary);
}
