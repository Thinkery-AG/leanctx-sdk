// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
use std::io::{Read, Write};
use std::sync::{mpsc, Arc, Mutex};

use serde_json::{Map, Value};

#[derive(Debug)]
pub(crate) enum ReaderMessage {
    Line(Vec<u8>),
    End,
    Overflow,
}

/// One framed request for the thread that owns the instance's stdin.
#[derive(Debug)]
pub(crate) struct WriteJob {
    pub(crate) bytes: Vec<u8>,
    pub(crate) done: mpsc::Sender<std::io::Result<()>>,
}

/// Number of live stdin writer threads; lets tests prove none stays blocked.
#[cfg(test)]
pub(crate) static ACTIVE_WRITERS: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);

#[cfg(test)]
struct ActiveWriter;

#[cfg(test)]
impl ActiveWriter {
    fn enter() -> Self {
        ACTIVE_WRITERS.fetch_add(1, std::sync::atomic::Ordering::AcqRel);
        Self
    }
}

#[cfg(test)]
impl Drop for ActiveWriter {
    fn drop(&mut self) {
        ACTIVE_WRITERS.fetch_sub(1, std::sync::atomic::Ordering::AcqRel);
    }
}

/// Owns the instance's stdin so that a blocking pipe write never holds a lock a
/// caller, a deadline or termination needs. The thread ends when every sender
/// is dropped (termination) or a write fails (the instance stopped reading and
/// was killed); either way stdin is closed here and nowhere else.
pub(crate) fn write_agent_stdin<W: Write>(mut writer: W, jobs: mpsc::Receiver<WriteJob>) {
    #[cfg(test)]
    let _active = ActiveWriter::enter();
    for job in jobs {
        let result = writer.write_all(&job.bytes).and_then(|_| writer.flush());
        let failed = result.is_err();
        let _ = job.done.send(result);
        if failed {
            return;
        }
    }
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
    request.insert("sdk_version".to_owned(), Value::String("1.3.0".to_owned()));
    Value::Object(request)
}

pub(crate) fn close_request() -> Value {
    let mut request = Map::new();
    request.insert("op".to_owned(), Value::String("close".to_owned()));
    Value::Object(request)
}

/// Session containment (POSIX): the request to the instance binary.
#[cfg_attr(not(unix), allow(dead_code))]
pub(crate) const SESSION_CONTAINMENT_ENV: &str = "LEAN_CTX_SESSION_CONTAINMENT";
/// The line the instance's session starter writes to standard output before the
/// instance itself is started.
pub(crate) const SESSION_CONTAINMENT_ACK_LINE: &[u8] = b"{\"leanctx_session_containment\":1}\n";

/// `contained` is set when the very first line is the session starter's
/// acknowledgement; that line is not a response and is not passed on.
pub(crate) fn read_agent_stdout<R: Read>(
    mut reader: R,
    sender: mpsc::Sender<ReaderMessage>,
    max_response_bytes: usize,
    contained: Arc<std::sync::atomic::AtomicBool>,
) {
    let ack = &SESSION_CONTAINMENT_ACK_LINE[..SESSION_CONTAINMENT_ACK_LINE.len() - 1];
    let mut first = true;
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
            if std::mem::take(&mut first) && line == ack {
                contained.store(true, std::sync::atomic::Ordering::Release);
                continue;
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

#[cfg(test)]
mod session_containment_tests {
    use super::{read_agent_stdout, ReaderMessage, SESSION_CONTAINMENT_ACK_LINE};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{mpsc, Arc};

    const RESPONSE: &[u8] = b"{\"id\":\"1\",\"ok\":true,\"result\":{\"text\":\"\xc3\xa9\"}}";

    /// Runs the stdout reader over `input`; returns the lines it passed on
    /// and whether it recorded the session starter's acknowledgement.
    fn read(input: &[u8]) -> (Vec<Vec<u8>>, bool) {
        let contained = Arc::new(AtomicBool::new(false));
        let (sender, receiver) = mpsc::channel();
        read_agent_stdout(input, sender, 1024 * 1024, Arc::clone(&contained));
        let mut lines = Vec::new();
        while let Ok(message) = receiver.recv() {
            match message {
                ReaderMessage::Line(line) => lines.push(line),
                ReaderMessage::End => break,
                ReaderMessage::Overflow => panic!("unexpected overflow"),
            }
        }
        (lines, contained.load(Ordering::Acquire))
    }

    #[test]
    fn without_an_acknowledgement_every_line_is_passed_on_unchanged() {
        let mut input = RESPONSE.to_vec();
        input.push(b'\n');
        let (lines, contained) = read(&input);
        assert_eq!(lines, vec![RESPONSE.to_vec()]);
        assert!(!contained);
    }

    #[test]
    fn a_leading_acknowledgement_is_recorded_and_is_not_a_response() {
        let mut input = SESSION_CONTAINMENT_ACK_LINE.to_vec();
        input.extend_from_slice(RESPONSE);
        input.push(b'\n');
        let (lines, contained) = read(&input);
        assert_eq!(lines, vec![RESPONSE.to_vec()]);
        assert!(contained);
    }

    #[test]
    fn only_the_first_line_can_be_the_acknowledgement() {
        let mut input = RESPONSE.to_vec();
        input.push(b'\n');
        input.extend_from_slice(SESSION_CONTAINMENT_ACK_LINE);
        let (lines, contained) = read(&input);
        assert_eq!(lines.len(), 2, "a later acknowledgement line is passed on");
        assert!(!contained);
    }
}
