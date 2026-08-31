use std::process::Child;

use crate::errors::{boxed, EngineExecutionError, SdkResult};

pub(crate) fn terminate_process_tree(child: &mut Child) -> SdkResult<()> {
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
                return Err(boxed(EngineExecutionError::new(
                    "Engine process group could not be terminated",
                )))
            }
        }
    }
    #[cfg(not(unix))]
    {
        if child.kill().is_err() && child.try_wait().ok().flatten().is_none() {
            return Err(boxed(EngineExecutionError::new(
                "Engine process could not be terminated",
            )));
        }
    }
    child.wait().map_err(|_| {
        boxed(EngineExecutionError::new(
            "Engine process could not be reaped",
        ))
    })?;
    Ok(())
}
