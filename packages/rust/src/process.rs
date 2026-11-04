use std::process::Child;
use std::thread;
use std::time::{Duration, Instant};

use crate::errors::{boxed, InstanceExecutionError, SdkResult};

/// How long the one cleanup owner waits for a terminated instance to be reaped.
pub(crate) const REAP_BUDGET: Duration = Duration::from_secs(5);

/// How long a `taskkill` helper that overran its deadline is given to be reaped.
#[cfg(windows)]
const TASKKILL_REAP_BUDGET: Duration = Duration::from_secs(2);

/// Reaps `child` within `budget`. The standard library has no timed wait, so
/// the exit status is polled. A child that is still running when the budget
/// ends is a reported cleanup failure, never an unbounded wait.
pub(crate) fn reap_within(child: &mut Child, budget: Duration) -> SdkResult<()> {
    let deadline = Instant::now() + budget;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return Ok(()),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(5)),
            Ok(None) => {
                return Err(boxed(InstanceExecutionError::new(
                    "LeanCTX instance process could not be reaped within its budget",
                )))
            }
            Err(_) => {
                return Err(boxed(InstanceExecutionError::new(
                    "LeanCTX instance process could not be reaped",
                )))
            }
        }
    }
}

/// How long an empty process group is waited for after the kill.
#[cfg(unix)]
const GROUP_CONFIRM_BUDGET: Duration = Duration::from_secs(1);

/// Whether the group has a member that is not a zombie. Only observes.
/// `None` when the process table could not be read.
#[cfg(unix)]
fn group_has_live_members(pgid: i32) -> Option<bool> {
    let output = std::process::Command::new("ps")
        .args(["-A", "-o", "pgid=", "-o", "stat="])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let wanted = pgid.to_string();
    Some(String::from_utf8_lossy(&output.stdout).lines().any(|line| {
        let mut fields = line.split_whitespace();
        fields.next() == Some(wanted.as_str())
            && fields.next().is_some_and(|state| !state.starts_with('Z'))
    }))
}

/// Records every group signal for the ownership tests. Test builds only.
#[cfg(all(test, unix))]
pub(crate) mod group_signal_log {
    use std::sync::Mutex;

    /// (group, whether the leader was still in the process table).
    pub(crate) static SENT: Mutex<Vec<(i32, bool)>> = Mutex::new(Vec::new());

    pub(super) fn record(pgid: i32) {
        let unreaped = std::process::Command::new("ps")
            .args(["-o", "stat=", "-p", &pgid.to_string()])
            .output()
            .map(|output| !String::from_utf8_lossy(&output.stdout).trim().is_empty())
            .unwrap_or(false);
        if let Ok(mut sent) = SENT.lock() {
            sent.push((pgid, unreaped));
        }
    }
}

pub(crate) fn terminate_process_tree(child: &mut Child) -> SdkResult<()> {
    #[cfg(unix)]
    let mut group_error = None;
    #[cfg(unix)]
    {
        use nix::errno::Errno;
        use nix::sys::signal::{killpg, Signal};
        use nix::unistd::Pid;

        let pid = i32::try_from(child.id()).map_err(|_| {
            boxed(InstanceExecutionError::new(
                "LeanCTX instance process group identifier is invalid",
            ))
        })?;
        if pid <= 0 {
            return Err(boxed(InstanceExecutionError::new(
                "LeanCTX instance process group identifier is invalid",
            )));
        }
        // Ownership rule. A process group is addressed by a number that is
        // this client's own only while the leader is unreaped: running or
        // exited, an unreaped leader pins it. Callers must not have reaped
        // `child` (`wait`, or `try_wait` returning a status) before this
        // call, and nothing in this block reaps: no `try_wait`, no `wait`.
        // The reap happens once, below, after the last signal, and nothing
        // is signalled after it.
        let signal_group = |pid: i32| {
            #[cfg(test)]
            group_signal_log::record(pid);
            killpg(Pid::from_raw(pid), Signal::SIGKILL)
        };
        match signal_group(pid) {
            Ok(()) => {
                // A member that was in the middle of a fork when the group was
                // signalled can leave a child the signal did not reach: signal
                // the same group again, briefly.
                for _ in 0..3 {
                    thread::sleep(Duration::from_millis(2));
                    if signal_group(pid).is_err() {
                        break;
                    }
                }
                // Confirm, still before the reap, that the group is empty and
                // signal again while it is not. If the process table cannot
                // be read the result stays unconfirmed and is not an error.
                let confirmed = Instant::now() + GROUP_CONFIRM_BUDGET;
                while group_has_live_members(pid) == Some(true) {
                    if Instant::now() >= confirmed {
                        group_error = Some(boxed(InstanceExecutionError::new(
                            "LeanCTX instance process group still has live members",
                        )));
                        break;
                    }
                    let _ = signal_group(pid);
                    thread::sleep(Duration::from_millis(10));
                }
            }
            Err(Errno::ESRCH) => {}
            // Darwin reports EPERM for a group whose only member is the
            // exited, unreaped leader — the normal state after a graceful
            // instance exit. That is settled by looking at the process table,
            // before the reap, never by signalling again after it.
            Err(Errno::EPERM) if group_has_live_members(pid) == Some(false) => {}
            Err(_) => {
                group_error = Some(boxed(InstanceExecutionError::new(
                    "LeanCTX instance process group could not be terminated",
                )));
                let _ = child.kill();
            }
        }
    }
    // Windows: `taskkill /T` ends the tree that is reachable through live
    // parent links, within a five-second budget. A tree kill that fails or
    // exceeds the budget is reported; exit status 128 means the instance had
    // already exited and is not a failure. Not covered: a descendant whose
    // parent already exited is unreachable through parent links. Closing that
    // gap needs a Job Object, which this crate cannot create while it forbids
    // `unsafe` and carries no Windows API dependency.
    #[cfg(windows)]
    let tree_failed = {
        use std::process::{Command, Stdio};

        // `failed` stays true unless taskkill itself reports success: a helper
        // that could not be started, overran its budget or could not be ended
        // is a failed tree kill.
        let mut failed = true;
        if let Ok(mut killer) = Command::new("taskkill")
            .args(["/F", "/T", "/PID", &child.id().to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            let deadline = Instant::now() + Duration::from_secs(5);
            loop {
                match killer.try_wait() {
                    Ok(Some(status)) => {
                        failed = !(status.success() || status.code() == Some(128));
                        break;
                    }
                    Ok(None) if Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(20));
                    }
                    _ => {
                        // End the helper and reap it within its own budget;
                        // never wait for it without one.
                        let _ = killer.kill();
                        let _ = reap_within(&mut killer, TASKKILL_REAP_BUDGET);
                        break;
                    }
                }
            }
        }
        failed
    };
    #[cfg(not(unix))]
    let kill_error = match child.try_wait() {
        Ok(Some(_)) => None,
        _ => child.kill().err(),
    };
    let wait_result = reap_within(child, REAP_BUDGET);
    #[cfg(unix)]
    if let Some(error) = group_error {
        wait_result?;
        return Err(error);
    }
    #[cfg(not(unix))]
    if kill_error.is_some() {
        wait_result?;
        return Err(boxed(InstanceExecutionError::new(
            "LeanCTX instance process could not be terminated",
        )));
    }
    #[cfg(windows)]
    if tree_failed {
        wait_result?;
        return Err(boxed(InstanceExecutionError::new(
            "LeanCTX instance process tree termination could not be verified",
        )));
    }
    wait_result?;
    Ok(())
}
