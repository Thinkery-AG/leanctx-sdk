//go:build !windows

package leanctx

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"syscall"
)

func configureProcessGroup(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

func terminateProcess(command *exec.Cmd) error {
	if command == nil || command.Process == nil {
		return nil
	}
	pid := command.Process.Pid
	var groupErr error
	if pid > 0 {
		groupErr = syscall.Kill(-pid, syscall.SIGKILL)
	}
	processErr := command.Process.Kill()
	if groupErr != nil && !errors.Is(groupErr, syscall.ESRCH) {
		return fmt.Errorf("process group could not be terminated: %w", groupErr)
	}
	if processErr != nil && !errors.Is(processErr, os.ErrProcessDone) {
		return fmt.Errorf("process could not be terminated: %w", processErr)
	}
	return nil
}
