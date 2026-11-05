//go:build windows

package leanctx

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"time"
)

// taskkillBudget bounds the tree kill; a hung taskkill is a failed tree kill.
const taskkillBudget = 5 * time.Second

// taskkillReapBudget bounds the wait for a taskkill helper that was killed at
// the end of its budget.
const taskkillReapBudget = 2 * time.Second

func configureProcessGroup(command *exec.Cmd) {}

// requestSessionContainment asks the Engine binary to contain its session. On
// Windows the Engine joins a kill-on-close job of its own and acknowledges on
// its first output line. It must be called after command.Env has been set. An
// Engine without the mode ignores the variable and writes no acknowledgement.
func requestSessionContainment(command *exec.Cmd) {
	command.Env = append(command.Env, sessionContainmentEnv+"=1")
}

// terminateProcess ends the Engine and the descendants that are reachable
// through live parent links (`taskkill /T`), then the Engine itself. A tree
// kill that fails or exceeds its budget while the Engine was still alive is
// reported: descendants may have survived and that must not pass as cleanup.
//
// Not covered: a descendant whose parent already exited is not reachable
// through parent links at all. Closing that gap needs a Job Object.
func terminateProcess(command *exec.Cmd) error {
	if command == nil || command.Process == nil {
		return nil
	}
	if command.ProcessState != nil {
		// Waited for already: the process id may belong to anyone now, and
		// taskkill addresses by number. Callers wait only after this returned.
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), taskkillBudget)
	defer cancel()
	treeKill := exec.CommandContext(ctx, "taskkill", "/F", "/T", "/PID", strconv.Itoa(command.Process.Pid))
	// When the budget ends the helper is killed; WaitDelay bounds the wait for
	// a helper that does not exit even then. Either case is an error from Run
	// and therefore a failed tree kill below.
	treeKill.WaitDelay = taskkillReapBudget
	treeErr := treeKill.Run()
	killErr := command.Process.Kill()
	if killErr != nil && !errors.Is(killErr, os.ErrProcessDone) {
		return fmt.Errorf("process could not be terminated: %w", killErr)
	}
	// taskkill exits with 128 when the Engine had already exited by itself;
	// that is not a failed tree kill. Every other failure, including the
	// exhausted budget, is.
	var exitErr *exec.ExitError
	if treeErr != nil && !(ctx.Err() == nil && errors.As(treeErr, &exitErr) && exitErr.ExitCode() == taskkillProcessNotFound) {
		return fmt.Errorf("process tree termination could not be verified: %w", treeErr)
	}
	return nil
}

// taskkillProcessNotFound is taskkill's exit status for an absent process.
const taskkillProcessNotFound = 128
