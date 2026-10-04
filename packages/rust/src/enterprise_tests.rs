// These loopback-only tests exercise the HTTP transport contract with synthetic
// Engine responses. They are not installed-user proof and make no remote calls.
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::{EnginePlanningRequest, EnterpriseEngineClient};
use crate::errors::{EngineProtocolError, EngineTimeout};
use crate::planning::canonical_bytes;
use crate::protocol::sha256_digest;

const TENANT_ID: &str = "11111111-1111-4111-8111-111111111111";
const OTHER_TENANT_ID: &str = "33333333-3333-4333-8333-333333333333";
const SOURCE_ID: &str = "22222222-2222-4222-8222-222222222222";
const OTHER_SOURCE_ID: &str = "44444444-4444-4444-8444-444444444444";
const CREDENTIAL: &str = "fixture-test-token";
const TASK_ID: &str = "contract-task-1";
const CONTENT: &str = "prepared context from a local fixture";
const GOVERNANCE_REVISION: u64 = 41;

struct CapturedRequest {
    headers: String,
    body: Vec<u8>,
}

impl CapturedRequest {
    fn path(&self) -> &str {
        self.headers
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .expect("request target")
    }

    fn header(&self, name: &str) -> Option<&str> {
        self.headers.lines().skip(1).find_map(|line| {
            let (key, value) = line.split_once(':')?;
            key.eq_ignore_ascii_case(name).then(|| value.trim())
        })
    }

    fn json_body(&self) -> Value {
        serde_json::from_slice(&self.body).expect("request JSON body")
    }
}

fn start_fixture(
    respond: impl FnOnce(&mut TcpStream) + Send + 'static,
) -> (String, JoinHandle<CapturedRequest>) {
    let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind loopback fixture");
    let address = listener.local_addr().expect("fixture address");
    let worker = thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept fixture request");
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .expect("set fixture read timeout");
        let request = read_request(&mut stream);
        respond(&mut stream);
        request
    });
    (format!("http://{address}"), worker)
}

fn read_request(stream: &mut TcpStream) -> CapturedRequest {
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 4096];
    let (header_end, body_end) = loop {
        let count = stream.read(&mut chunk).expect("read fixture request");
        assert_ne!(count, 0, "client closed before sending a complete request");
        bytes.extend_from_slice(&chunk[..count]);
        if let Some(header_end) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            let headers = String::from_utf8_lossy(&bytes[..header_end]);
            let content_length = headers
                .lines()
                .skip(1)
                .find_map(|line| {
                    let (key, value) = line.split_once(':')?;
                    key.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().expect("valid content length"))
                })
                .unwrap_or(0);
            let body_start = header_end + 4;
            let body_end = body_start + content_length;
            if bytes.len() >= body_end {
                break (header_end, body_end);
            }
        }
    };
    CapturedRequest {
        headers: String::from_utf8_lossy(&bytes[..header_end]).into_owned(),
        body: bytes[header_end + 4..body_end].to_vec(),
    }
}

fn write_response(
    stream: &mut TcpStream,
    status: &str,
    headers: &[(&str, &str)],
    declared_length: usize,
    body: &[u8],
) {
    write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {declared_length}\r\nConnection: close\r\n"
    )
    .expect("write fixture status and headers");
    for (name, value) in headers {
        write!(stream, "{name}: {value}\r\n").expect("write fixture header");
    }
    stream.write_all(b"\r\n").expect("finish fixture headers");
    stream.write_all(body).expect("write fixture response");
    stream.flush().expect("flush fixture response");
}

fn test_client(base_url: &str, timeout: Duration) -> EnterpriseEngineClient {
    EnterpriseEngineClient::with_options(base_url, CREDENTIAL, TENANT_ID, timeout, true)
        .expect("configure loopback-only fixture client")
}

fn planning_request() -> EnginePlanningRequest {
    EnginePlanningRequest::new(TASK_ID, "summarize the selected source", 10)
        .expect("valid planning request")
}

fn source_plan(source_id: &str) -> (Value, String) {
    let content_digest = sha256_digest(b"fixture source at git revision fixture-revision-7");
    let mut projection = json!({
        "schema_version": 1,
        "context_plan_id": "contract-plan-1",
        "task_id": TASK_ID,
        "budget_tokens": 10,
        "selections": [{
            "source_ref": source_id,
            "provider": source_id,
            "disposition": "selected",
            "token_count": 2,
            "sha256_digest": content_digest,
            "reason_codes": ["relevant"]
        }]
    });
    let projection_digest =
        sha256_digest(&canonical_bytes(&projection).expect("canonical fixture projection"));
    projection
        .as_object_mut()
        .expect("fixture projection object")
        .insert(
            "projection_digest".to_owned(),
            Value::String(projection_digest),
        );

    let binding = json!({
        "object_ref": source_id,
        "source_id": source_id,
        "source_type": "filesystem",
        "content_digest": content_digest,
        "revision": "git:fixture-revision-7",
        "owner": "fixture-owner",
        "observed_at": null,
        "valid_until": null,
        "classification": "Internal",
        "permission": "permitted"
    });
    let result = json!({
        "schema_version": 1,
        "transport_version": 1,
        "engine_interface_version": "1.0.0",
        "plan": projection
    });
    let bindings = vec![binding];
    let binding_digest = sha256_digest(
        &canonical_bytes(&json!([result.clone(), bindings.clone()]))
            .expect("canonical fixture bindings"),
    );
    (
        json!({
            "result": result,
            "source_bindings": bindings,
            "binding_digest": binding_digest
        }),
        binding_digest,
    )
}

fn plan_response(tenant_id: &str, governance_revision: u64, source_id: &str) -> Value {
    let (plan, _) = source_plan(source_id);
    json!({
        "schema_version": 1,
        "tenant_id": tenant_id,
        "governance_revision": governance_revision,
        "plan": plan
    })
}

fn materialization_response(
    tenant_id: &str,
    governance_revision: u64,
    source_id: &str,
    content: &str,
) -> (Value, String) {
    let (plan, binding_digest) = source_plan(source_id);
    (
        json!({
            "schema_version": 1,
            "tenant_id": tenant_id,
            "governance_revision": governance_revision,
            "materialization": {
                "schema_version": 1,
                "transport_version": 1,
                "engine_interface_version": "1.0.0",
                "plan": plan,
                "materialized_digest": sha256_digest(content.as_bytes()),
                "materialized_token_count": 2,
                "content": content
            }
        }),
        binding_digest,
    )
}

fn is_protocol_error(error: &(dyn std::error::Error + 'static)) -> bool {
    error.downcast_ref::<EngineProtocolError>().is_some()
}

#[test]
fn plan_post_binds_tenant_sources_revision_and_validated_projection() {
    let response = plan_response(TENANT_ID, u64::MAX, SOURCE_ID);
    let body = serde_json::to_vec(&response).expect("serialize plan fixture");
    let (url, server) = start_fixture(move |stream| {
        write_response(stream, "200 OK", &[], body.len(), &body);
    });

    let result =
        test_client(&url, Duration::from_secs(2)).context_plan(&planning_request(), &[SOURCE_ID]);
    let request = server.join().expect("fixture server");
    let plan = result.expect("validated source-plan response");

    assert_eq!(request.path(), "/v1/engine/context-plan");
    assert_eq!(
        request.header("Authorization"),
        Some("Bearer fixture-test-token")
    );
    assert_eq!(request.header("Content-Type"), Some("application/json"));
    let posted: Value = request.json_body();
    assert_eq!(posted["source_ids"], json!([SOURCE_ID]));
    assert_eq!(posted["planning"]["schema_version"], 1);
    assert_eq!(posted["planning"]["transport_version"], 1);
    assert_eq!(posted["planning"]["engine_interface_version"], "1.0.0");
    assert_eq!(posted["planning"]["task_id"], TASK_ID);
    assert_eq!(plan.tenant_id(), TENANT_ID);
    assert_eq!(plan.governance_revision(), u64::MAX);
    assert_eq!(
        plan.plan()["source_bindings"][0]["revision"],
        "git:fixture-revision-7"
    );
    assert_eq!(plan.plan()["source_bindings"][0]["permission"], "permitted");
}

#[test]
fn plan_rejects_wrong_tenant_and_out_of_scope_source_bindings() {
    for (tenant_id, source_id) in [(OTHER_TENANT_ID, SOURCE_ID), (TENANT_ID, OTHER_SOURCE_ID)] {
        let response = plan_response(tenant_id, GOVERNANCE_REVISION, source_id);
        let body = serde_json::to_vec(&response).expect("serialize plan fixture");
        let (url, server) = start_fixture(move |stream| {
            write_response(stream, "200 OK", &[], body.len(), &body);
        });
        let error = test_client(&url, Duration::from_secs(2))
            .context_plan(&planning_request(), &[SOURCE_ID])
            .expect_err("tenant/source binding mismatch must fail closed");
        server.join().expect("fixture server");
        assert!(is_protocol_error(error.as_ref()));
    }
}

#[test]
fn materialization_post_binds_revision_source_digest_and_content() {
    let (response, binding_digest) =
        materialization_response(TENANT_ID, GOVERNANCE_REVISION, SOURCE_ID, CONTENT);
    let body = serde_json::to_vec(&response).expect("serialize materialization fixture");
    let (url, server) = start_fixture(move |stream| {
        write_response(stream, "200 OK", &[], body.len(), &body);
    });

    let materialization = test_client(&url, Duration::from_secs(2))
        .context_materialize(
            &planning_request(),
            &[SOURCE_ID],
            GOVERNANCE_REVISION,
            &binding_digest,
            Some("2026-09-23T12:00:00Z"),
        )
        .expect("validated materialization response");
    let request = server.join().expect("fixture server");

    assert_eq!(request.path(), "/v1/engine/context-materialize");
    assert_eq!(
        request.header("Authorization"),
        Some("Bearer fixture-test-token")
    );
    let posted = request.json_body();
    assert_eq!(posted["source_ids"], json!([SOURCE_ID]));
    assert_eq!(posted["expected_governance_revision"], GOVERNANCE_REVISION);
    assert_eq!(posted["expected_binding_digest"], binding_digest);
    assert_eq!(posted["planning_evaluation_time"], "2026-09-23T12:00:00Z");
    assert_eq!(materialization.tenant_id(), TENANT_ID);
    assert_eq!(materialization.governance_revision(), GOVERNANCE_REVISION);
    assert_eq!(materialization.content(), Some(CONTENT));
    assert_eq!(materialization.materialized_token_count(), Some(2));
}

#[test]
fn materialization_rejects_wrong_revision_and_binding_digest() {
    let (response, binding_digest) =
        materialization_response(TENANT_ID, GOVERNANCE_REVISION + 1, SOURCE_ID, CONTENT);
    let body = serde_json::to_vec(&response).expect("serialize materialization fixture");
    let (url, server) = start_fixture(move |stream| {
        write_response(stream, "200 OK", &[], body.len(), &body);
    });
    let error = test_client(&url, Duration::from_secs(2))
        .context_materialize(
            &planning_request(),
            &[SOURCE_ID],
            GOVERNANCE_REVISION,
            &binding_digest,
            None,
        )
        .expect_err("governance revision mismatch must fail closed");
    server.join().expect("fixture server");
    assert!(is_protocol_error(error.as_ref()));

    let (response, actual_binding_digest) =
        materialization_response(TENANT_ID, GOVERNANCE_REVISION, SOURCE_ID, CONTENT);
    let body = serde_json::to_vec(&response).expect("serialize materialization fixture");
    let (url, server) = start_fixture(move |stream| {
        write_response(stream, "200 OK", &[], body.len(), &body);
    });
    let wrong_binding_digest = sha256_digest(b"different source bindings");
    assert_ne!(wrong_binding_digest, actual_binding_digest);
    let error = test_client(&url, Duration::from_secs(2))
        .context_materialize(
            &planning_request(),
            &[SOURCE_ID],
            GOVERNANCE_REVISION,
            &wrong_binding_digest,
            None,
        )
        .expect_err("binding digest mismatch must fail closed");
    server.join().expect("fixture server");
    assert!(is_protocol_error(error.as_ref()));
}

#[test]
fn malformed_and_oversized_responses_are_rejected() {
    let malformed = b"{\"schema_version\": }".to_vec();
    let (url, server) = start_fixture(move |stream| {
        write_response(stream, "200 OK", &[], malformed.len(), &malformed);
    });
    let error = test_client(&url, Duration::from_secs(2))
        .context_plan(&planning_request(), &[SOURCE_ID])
        .expect_err("malformed JSON must fail closed");
    server.join().expect("fixture server");
    assert!(is_protocol_error(error.as_ref()));

    let (url, server) = start_fixture(|stream| {
        write_response(stream, "200 OK", &[], 1024 * 1024 + 1, &[]);
    });
    let error = test_client(&url, Duration::from_secs(2))
        .context_plan(&planning_request(), &[SOURCE_ID])
        .expect_err("oversized Content-Length must fail before body allocation/read");
    server.join().expect("fixture server");
    assert!(is_protocol_error(error.as_ref()));

    let (response, _) = materialization_response(
        TENANT_ID,
        GOVERNANCE_REVISION,
        SOURCE_ID,
        &"x".repeat(1024 * 1024 + 1),
    );
    let body = serde_json::to_vec(&response).expect("serialize oversized content fixture");
    assert!(body.len() < 2 * 1024 * 1024);
    let (url, server) = start_fixture(move |stream| {
        write_response(stream, "200 OK", &[], body.len(), &body);
    });
    let (_, binding_digest) =
        materialization_response(TENANT_ID, GOVERNANCE_REVISION, SOURCE_ID, CONTENT);
    let error = test_client(&url, Duration::from_secs(4))
        .context_materialize(
            &planning_request(),
            &[SOURCE_ID],
            GOVERNANCE_REVISION,
            &binding_digest,
            None,
        )
        .expect_err("oversized materialized content must fail closed");
    server.join().expect("fixture server");
    assert!(is_protocol_error(error.as_ref()));
}

#[test]
fn redirect_is_not_followed_and_target_receives_no_credentials() {
    let target = TcpListener::bind(("127.0.0.1", 0)).expect("bind redirect target");
    target
        .set_nonblocking(true)
        .expect("make redirect target nonblocking");
    let target_url = format!("http://{}/capture", target.local_addr().unwrap());
    let (url, server) = start_fixture(move |stream| {
        write_response(
            stream,
            "302 Found",
            &[("Location", target_url.as_str())],
            0,
            &[],
        );
    });
    let error = test_client(&url, Duration::from_secs(2))
        .context_plan(&planning_request(), &[SOURCE_ID])
        .expect_err("redirect response must be rejected");
    server.join().expect("origin fixture server");
    assert!(is_protocol_error(error.as_ref()));
    match target.accept() {
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
        Ok(_) => panic!("redirect target received an HTTP request and may see credentials"),
        Err(error) => panic!("redirect target check failed: {error}"),
    }
}

#[test]
fn trickled_response_body_cannot_extend_the_full_request_deadline() {
    let (url, server) = start_fixture(|stream| {
        write_response(stream, "200 OK", &[], 10, &[]);
        for byte in b"0123456789" {
            if stream.write_all(&[*byte]).is_err() || stream.flush().is_err() {
                break;
            }
            thread::sleep(Duration::from_millis(90));
        }
    });
    let timeout = Duration::from_millis(250);
    let started = Instant::now();
    let error = test_client(&url, timeout)
        .context_plan(&planning_request(), &[SOURCE_ID])
        .expect_err("a trickled full body must respect the total deadline");
    let elapsed = started.elapsed();
    server.join().expect("fixture server");

    assert!(error.downcast_ref::<EngineTimeout>().is_some());
    assert!(
        elapsed < Duration::from_millis(700),
        "deadline took {elapsed:?}"
    );
}

#[test]
fn delayed_headers_then_stalled_body_respect_one_request_deadline() {
    let (url, server) = start_fixture(|stream| {
        thread::sleep(Duration::from_millis(300));
        write_response(stream, "200 OK", &[], 1, &[]);
        thread::sleep(Duration::from_millis(800));
    });
    let timeout = Duration::from_millis(500);
    let started = Instant::now();
    let error = test_client(&url, timeout)
        .context_plan(&planning_request(), &[SOURCE_ID])
        .expect_err("a stalled body after delayed headers must respect the request deadline");
    let elapsed = started.elapsed();
    server.join().expect("fixture server");

    assert!(error.downcast_ref::<EngineTimeout>().is_some());
    assert!(
        elapsed < Duration::from_millis(700),
        "delayed-header body deadline took {elapsed:?}"
    );
}
