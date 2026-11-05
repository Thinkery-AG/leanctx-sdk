// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
//! Test-harness environment: the LEANCTX_*_INSTANCE_* name is read first; the
//! deprecated LEANCTX_*_ENGINE_* name is the fallback, with a one-time warning.

use std::collections::BTreeSet;
use std::ffi::OsString;
use std::sync::Mutex;

static WARNED: Mutex<BTreeSet<&'static str>> = Mutex::new(BTreeSet::new());

pub fn instance_env(name: &'static str, deprecated: &'static str) -> Option<OsString> {
    if let Some(value) = std::env::var_os(name) {
        return Some(value);
    }
    let value = std::env::var_os(deprecated)?;
    let first = WARNED
        .lock()
        .map(|mut warned| warned.insert(deprecated))
        .unwrap_or(false);
    if first {
        eprintln!("warning: {deprecated} is deprecated since SDK 1.3.0; use {name}");
    }
    Some(value)
}
