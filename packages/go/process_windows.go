//go:build windows

package leanctx

import "os/exec"

func configureProcessGroup(command *exec.Cmd) {}

func terminateProcess(command *exec.Cmd) {
	if command != nil && command.Process != nil {
		_ = command.Process.Kill()
	}
}
