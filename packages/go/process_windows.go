//go:build windows

package leanctx

import (
	"errors"
	"os"
	"os/exec"
)

func configureProcessGroup(command *exec.Cmd) {}

func terminateProcess(command *exec.Cmd) error {
	if command != nil && command.Process != nil {
		if err := command.Process.Kill(); err != nil && !errors.Is(err, os.ErrProcessDone) {
			return err
		}
	}
	return nil
}
