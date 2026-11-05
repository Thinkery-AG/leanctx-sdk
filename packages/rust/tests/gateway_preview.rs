// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

// Source resolution: private preparation, HostReported and restricted refusal cases retained.
// Public context-store tests are added separately in context_store_preview.rs.
//! Gateway preview conformance (fixtures shared by all six SDKs) and live instance.

use std::fs;
use std::path::PathBuf;

mod support;
use support::instance_env;

use leanctx_sdk::preview::{
    keyed_object_digest, parse_egress_admission, parse_egress_host_report,
    parse_egress_preparation, receipt_key_id, EgressHostReportRequest, EgressRequest,
};
use leanctx_sdk::{InstanceExecutionError, InstanceProtocolError, SubprocessInstanceClient};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

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
fn receipt_v2_objects_are_keyed_like_the_engine_keys_them() {
    let manifest = manifest();
    let vector = &manifest["keyed_v2"];
    let hex = vector["key_hex"].as_str().expect("key");
    let key: [u8; 32] =
        std::array::from_fn(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).expect("hex key"));
    let plain = format!(
        "sha256:{:x}",
        Sha256::digest(vector["object_text"].as_str().expect("text").as_bytes())
    );
    let keyed = vector["keyed_object"].as_str().expect("keyed");
    assert_eq!(receipt_key_id(&key), vector["key_id"]);
    assert_eq!(keyed_object_digest(&key, &plain).expect("digest"), keyed);
    assert_ne!(
        keyed_object_digest(&[0; 32], &plain).expect("digest"),
        keyed
    );
    let fixture = vector["fixture"].as_str().expect("fixture");
    let admission = parse_egress_admission(&load("valid", fixture)).expect("v2");
    let receipt = admission.receipt.expect("receipt");
    let object_digest = receipt.object_digest.as_ref().expect("v2 names its key");
    assert_eq!(receipt.schema_version, 2);
    assert_eq!(object_digest.algorithm, "hmac-sha256");
    assert_eq!(object_digest.key_id, vector["key_id"]);
    let objects: Vec<&str> = receipt
        .decisions
        .iter()
        .map(|d| d.object.as_str())
        .collect();
    assert_eq!(objects, [keyed]);
    assert!(
        !objects.contains(&plain.as_str()),
        "a plain guess confirms nothing"
    );
    let v1 = parse_egress_admission(&load("valid", "masked")).expect("v1");
    let v1 = v1.receipt.expect("receipt");
    assert_eq!((v1.schema_version, v1.object_digest), (1, None));
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
fn egress_prepare_preserves_text_and_report_is_host_reported() {
    let prepared = r#"{ "model" : "m" }"#;
    let final_context = format!("sha256:{:x}", Sha256::digest(prepared.as_bytes()));
    let decision = json!({
        "schema_version": 1,
        "disposition": "forward",
        "body": {"model": "m"},
        "classification": "internal",
        "receipt": {
            "schema_version": 1, "receipt_id": "egress-test", "mode": "governed",
            "principal": {"kind": "unknown"},
            "destination": {"provider": "openai", "locality": "remote"},
            "sources": {"inspected": 0, "permitted": 0, "selected": 0, "blocked": 0},
            "security": {"redactions": 0, "blocked_objects": 0, "quarantined_objects": 0,
                "injection_signals": 0, "incomplete_coverage": 0},
            "tokens": {"original": 0, "delivered": 0}, "outcome": "delivered",
            "duration_us": 0, "final_context": final_context,
        }
    });
    let parsed = parse_egress_preparation(&json!({
        "schema_version": 2, "decision": decision, "prepared_body": prepared,
        "admission_receipt_digest": format!("sha256:{}", "b".repeat(64)),
    }))
    .expect("preparation");
    assert_eq!(parsed.prepared_body.as_deref(), Some(prepared));
    assert!(parsed.may_send());
    assert!(!parse_egress_preparation(&json!({"schema_version":2,"decision":{"schema_version":1,"disposition":"refused","refusal":"withheld"}}))
        .expect("refused preparation").may_send());
    assert!(parse_egress_preparation(&json!({
        "schema_version": 2,
        "decision": {"schema_version": 1, "disposition": "forward", "body": {"model": "m"}},
        "prepared_body": prepared,
    }))
    .expect("receipt-less best-effort forward")
    .may_send());
    let mut rewritten = decision.clone();
    rewritten["disposition"] = json!("rewritten");
    assert!(parse_egress_preparation(&json!({
        "schema_version": 2, "decision": rewritten, "prepared_body": prepared,
    }))
    .expect("delivered rewritten preparation")
    .may_send());
    for disposition in ["forward", "rewritten"] {
        for outcome in ["withheld", "failed"] {
            let mut receipt = decision["receipt"].clone();
            receipt["outcome"] = json!(outcome);
            receipt
                .as_object_mut()
                .expect("receipt")
                .remove("final_context");
            let mut invalid = decision.clone();
            invalid["disposition"] = json!(disposition);
            invalid["receipt"] = receipt;
            assert!(
                parse_egress_preparation(&json!({
                    "schema_version": 2, "decision": invalid, "prepared_body": prepared,
                }))
                .is_err(),
                "{disposition} with {outcome} receipt was accepted"
            );
        }
    }
    assert!(
        parse_egress_preparation(&json!({
            "schema_version": 2,
            "decision": {
                "schema_version": 1, "disposition": "refused", "refusal": "withheld",
                "receipt": decision["receipt"].clone(),
            },
        }))
        .is_err(),
        "refused decision with delivered receipt was accepted"
    );

    let request = EgressHostReportRequest {
        admission_receipt_digest: format!("sha256:{}", "b".repeat(64)),
        attempt: 2,
        transport: "http".into(),
        result: "no_response_observed".into(),
        http_status: None,
        failure: Some("timeout".into()),
    };
    let observation = json!({
        "schema_version": 1, "admission_receipt_digest": request.admission_receipt_digest,
        "attempt": 2, "transport": "http", "observer": "host_reported",
        "result": "no_response_observed", "failure": "timeout",
    });
    let observation_digest = format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&observation).unwrap())
    );
    let response = json!({"schema_version": 1, "observation_digest": observation_digest,
        "observation": observation});
    let report = parse_egress_host_report(&response, &request).expect("host report");
    assert_eq!(report.observation["observer"], "host_reported");
    assert_eq!(report.observation["result"], "no_response_observed");
    let mut invalid = response;
    invalid["observation"]["observer"] = json!("engine_observed");
    assert!(parse_egress_host_report(&invalid, &request).is_err());
}

#[test]
fn every_invalid_case_is_rejected() {
    for name in manifest()["invalid"].as_array().expect("invalid") {
        let name = name.as_str().expect("name");
        let error = parse_egress_admission(&load("invalid", name)).expect_err(name);
        assert!(
            error.downcast_ref::<InstanceProtocolError>().is_some(),
            "{name}: {error}"
        );
    }
}

#[test]
fn live_engine_masks_withholds_and_classifies() {
    let Some(binary) = instance_env("LEANCTX_INSTANCE_BINARY", "LEANCTX_ENGINE_BINARY") else {
        eprintln!("skipped: needs a real instance (LEANCTX_INSTANCE_BINARY)");
        return;
    };
    let client = SubprocessInstanceClient::with_binary(binary).expect("client");
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
    assert_eq!(restricted.disposition, "refused");
    assert!(!restricted.may_send());
    assert!(restricted.body.is_none());
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

    let model = "host-selected-model-2026";
    let credential = format!("AKIA{}", "Q3EGRZ7LIVEX4KEY");
    let request = EgressRequest {
        provider: "openai".to_owned(),
        upstream_base: "https://api.openai.com".to_owned(),
        body: json!({
            "model": model,
            "messages": [{"role": "user", "content": format!("deploy fails with {credential}")}]
        })
        .as_object()
        .expect("request body object")
        .clone(),
    };
    let preparation = client
        .prepare_egress(&root, &request)
        .expect("egress preparation");
    assert!(preparation.may_send());
    let prepared_body = preparation.prepared_body.as_deref().expect("prepared body");
    let admission_digest = preparation
        .admission_receipt_digest
        .as_deref()
        .expect("admission receipt digest");
    let prepared: Value = serde_json::from_str(prepared_body).expect("prepared JSON body");
    assert_eq!(prepared["model"], model);
    assert!(!prepared_body.contains(&credential));
    let receipt = preparation
        .decision
        .receipt
        .as_ref()
        .expect("preparation receipt");
    assert_eq!(receipt.security["redactions"], 1);
    assert_eq!(receipt.destination.provider, "openai");
    assert_eq!(receipt.destination.model.as_deref(), Some(model));
    let body_digest = format!("sha256:{:x}", Sha256::digest(prepared_body.as_bytes()));
    assert_eq!(receipt.final_context.as_deref(), Some(body_digest.as_str()));

    let report = EgressHostReportRequest {
        admission_receipt_digest: admission_digest.to_owned(),
        attempt: 1,
        transport: "http".to_owned(),
        result: "no_response_observed".to_owned(),
        http_status: None,
        failure: Some("timeout".to_owned()),
    };
    let first = client
        .report_egress(&root, &report)
        .expect("host timeout report");
    let replay = client
        .report_egress(&root, &report)
        .expect("identical report replay");
    assert_eq!(first.observation["observer"], "host_reported");
    assert_eq!(first.observation["result"], "no_response_observed");
    assert_eq!(first.observation["failure"], "timeout");
    assert_eq!(first.observation_digest, replay.observation_digest);

    let conflict = EgressHostReportRequest {
        result: "response_observed".to_owned(),
        http_status: Some(503),
        failure: None,
        ..report
    };
    let conflict_error = client
        .report_egress(&root, &conflict)
        .expect_err("a contradictory report for the same attempt must be rejected");
    assert!(conflict_error
        .downcast_ref::<InstanceExecutionError>()
        .is_some());
    assert!(conflict_error
        .to_string()
        .contains("egress_observation_unavailable"));
    let _ = fs::remove_dir_all(&root);
}
