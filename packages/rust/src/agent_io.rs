// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
use std::io::Read;
use std::sync::{mpsc, Arc, Mutex};

use serde_json::{Map, Value};

#[derive(Debug)]
pub(crate) enum ReaderMessage {
    Line(Vec<u8>),
    End,
    Overflow,
}

pub(crate) fn hello_request(
    interface_version: &str,
    schema_version: u64,
    transport_version: u64,
) -> Value {
    let mut request = Map::new();
    request.insert("op".to_owned(), Value::String("hello".to_owned()));
    request.insert("schema_version".to_owned(), Value::from(schema_version));
    request.insert(
        "transport_version".to_owned(),
        Value::from(transport_version),
    );
    request.insert(
        "agent_tools_interface_version".to_owned(),
        Value::String(interface_version.to_owned()),
    );
    request.insert("sdk_version".to_owned(), Value::String("1.1.0".to_owned()));
    Value::Object(request)
}

pub(crate) fn close_request() -> Value {
    let mut request = Map::new();
    request.insert("op".to_owned(), Value::String("close".to_owned()));
    Value::Object(request)
}

pub(crate) fn read_agent_stdout<R: Read>(
    mut reader: R,
    sender: mpsc::Sender<ReaderMessage>,
    max_response_bytes: usize,
) {
    let mut pending = Vec::new();
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        let size = match reader.read(&mut chunk) {
            Ok(0) => {
                let _ = sender.send(ReaderMessage::End);
                return;
            }
            Ok(size) => size,
            Err(_) => {
                let _ = sender.send(ReaderMessage::End);
                return;
            }
        };
        pending.extend_from_slice(&chunk[..size]);
        if pending.len() > max_response_bytes + 1 {
            let _ = sender.send(ReaderMessage::Overflow);
            return;
        }
        while let Some(position) = pending.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = pending.drain(..position).collect();
            pending.drain(..1);
            if line.len() > max_response_bytes {
                let _ = sender.send(ReaderMessage::Overflow);
                return;
            }
            if sender.send(ReaderMessage::Line(line)).is_err() {
                return;
            }
        }
    }
}

pub(crate) fn read_agent_stderr<R: Read>(
    mut reader: R,
    target: Arc<Mutex<Vec<u8>>>,
    max_stderr_bytes: usize,
) {
    let mut chunk = [0_u8; 8192];
    loop {
        match reader.read(&mut chunk) {
            Ok(0) | Err(_) => return,
            Ok(size) => {
                if let Ok(mut target) = target.lock() {
                    let available = max_stderr_bytes.saturating_sub(target.len());
                    target.extend_from_slice(&chunk[..size.min(available)]);
                }
            }
        }
    }
}
