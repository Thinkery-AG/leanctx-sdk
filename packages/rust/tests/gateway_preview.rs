// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
//! Gateway preview conformance (fixtures shared by all six SDKs) and live Engine.

use std::fs;
use std::path::PathBuf;

use leanctx_sdk::preview::{parse_egress_admission, EgressRequest};
use leanctx_sdk::{EngineProtocolError, SubprocessEngineClient};
use serde_json::{json, Value};

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/gateway-preview-v1")
}

fn load(kind: &str, name: &str) -> Value {
    let raw = fs::read(fixtures().join(kind).join(format!("{name}.json"))).expect("fixture");
    serde_json::from_slice(&raw).expect("fixture JSON")
}

fn manifest() -> Value {
    serde_json::from_slice(&fs::read(fixtures().join("manifest.json")).expect("manifest"))
        .expect("manifest JSON")
}

#[test]
fn real_engine_responses_parse_with_their_recorded_shape() {
    let manifest = manifest();
    for (name, expected) in manifest["valid"].as_object().expect("valid") {
        let admission = parse_egress_admission(&load("valid", name)).expect(name);
        let receipt = admission.receipt.as_ref().expect("receipt");
        assert_eq!(admission.disposition, expected["disposition"], "{name}");
        assert_eq!(
            admission.classification.as_deref(),
            expected["classification"].as_str(),
            "{name}"
        );
        assert_eq!(receipt.outcome, expected["outcome"], "{name}");
        assert_eq!(
            Some(receipt.decisions.len() as u64),
            expected["decisions"].as_u64()
        );
        let denied = receipt
            .decisions
            .iter()
            .filter(|d| d.disposition == "deny")
            .count();
        assert_eq!(Some(denied as u64), expected["denied"].as_u64(), "{name}");
        assert_eq!(
            Some(receipt.security["redactions"]),
            expected["redactions"].as_u64()
        );
        assert_eq!(
            Some(receipt.signals().count() as u64),
            expected["signals"].as_u64()
        );
        assert!(
            admission.may_send() && !receipt.principal.is_known(),
            "{name}"
        );
    }
}

#[test]
fn every_invalid_case_is_rejected() {
    for name in manifest()["invalid"].as_array().expect("invalid") {
        let name = name.as_str().expect("name");
        let error = parse_egress_admission(&load("invalid", name)).expect_err(name);
        assert!(
            error.downcast_ref::<EngineProtocolError>().is_some(),
            "{name}: {error}"
        );
    }
}

#[test]
fn live_engine_masks_withholds_and_classifies() {
    let Some(binary) = std::env::var_os("LEANCTX_ENGINE_BINARY") else {
        eprintln!("skipped: needs a real Engine (LEANCTX_ENGINE_BINARY)");
        return;
    };
    let client = SubprocessEngineClient::with_binary(binary).expect("client");
    let root = std::env::temp_dir().join(format!("leanctx-gateway-rs-{}", std::process::id()));
    fs::create_dir_all(&root).expect("root");
    let admit = |text: &str, provider: &str, base: &str| {
        let body = json!({"model": "m", "temperature": 0.2,
                          "messages": [{"role": "user", "content": text}]});
        client
            .admit_egress(
                &root,
                &EgressRequest {
                    provider: provider.to_owned(),
                    upstream_base: base.to_owned(),
                    body: body.as_object().cloned().expect("object"),
                },
            )
            .expect("admission")
    };
    let credential = format!("AKIA{}", "Q3EGRZ7LIVEX4KEY");
    let masked = admit(
        &format!("deploy fails with {credential}"),
        "openai",
        "https://api.openai.com",
    );
    assert_eq!(masked.disposition, "rewritten");
    assert!(!Value::Object(masked.body.clone().expect("body"))
        .to_string()
        .contains(&credential));
    assert_eq!(
        masked.receipt.as_ref().expect("receipt").security["redactions"],
        1
    );

    let restricted = admit(
        "Classification: Secret\nroot cause and customer list",
        "anthropic",
        "https://api.anthropic.com",
    );
    let receipt = restricted.receipt.as_ref().expect("receipt");
    assert_eq!(restricted.classification.as_deref(), Some("restricted"));
    assert_eq!(receipt.outcome, "withheld");
    assert!(!Value::Object(restricted.body.clone().expect("body"))
        .to_string()
        .contains("customer list"));
    assert!(receipt.decisions[0]
        .reason_codes
        .iter()
        .any(|c| c == "destination.remote_restricted"));

    let marked = admit(
        "Classification: Confidential\nboard minutes",
        "openai",
        "https://api.openai.com",
    );
    assert_eq!(marked.disposition, "forward");
    assert_eq!(marked.classification.as_deref(), Some("confidential"));
    assert_eq!(
        marked
            .receipt
            .as_ref()
            .expect("receipt")
            .destination
            .locality,
        "remote"
    );
    let _ = fs::remove_dir_all(&root);
}
