use std::process::Child;

use crate::errors::{boxed, EngineExecutionError, SdkResult};

pub(crate) fn terminate_process_tree(child: &mut Child) -> SdkResult<()> {
    #[cfg(unix)]
    let mut group_error = None;
    #[cfg(unix)]
    {
        use nix::errno::Errno;
        use nix::sys::signal::{killpg, Signal};
        use nix::unistd::Pid;

        let pid = i32::try_from(child.id()).map_err(|_| {
            boxed(EngineExecutionError::new(
                "Engine process group identifier is invalid",
            ))
        })?;
        if pid <= 0 {
            return Err(boxed(EngineExecutionError::new(
                "Engine process group identifier is invalid",
            )));
        }
        match killpg(Pid::from_raw(pid), Signal::SIGKILL) {
            Ok(()) | Err(Errno::ESRCH) => {}
            Err(_) => {
                group_error = Some(boxed(EngineExecutionError::new(
                    "Engine process group could not be terminated",
                )));
                let _ = child.kill();
            }
        }
    }
    #[cfg(not(unix))]
    let kill_error = child.kill().err();
    let wait_result = child.wait().map_err(|_| {
        boxed(EngineExecutionError::new(
            "Engine process could not be reaped",
        ))
    });
    #[cfg(unix)]
    if let Some(error) = group_error {
        wait_result?;
        return Err(error);
    }
    #[cfg(not(unix))]
    if kill_error.is_some() {
        wait_result?;
        return Err(boxed(EngineExecutionError::new(
            "Engine process could not be terminated",
        )));
    }
    wait_result?;
    Ok(())
}
