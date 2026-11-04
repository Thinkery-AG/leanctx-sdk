//go:build !windows

package leanctx

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	groupKillRepeats = 3
	groupKillPause   = 2 * time.Millisecond
	// groupConfirmBudget bounds the wait for an empty group after the kill.
	groupConfirmBudget = time.Second
)

// groupSignal sends a signal to a process group. It is a variable so that
// tests can record every group signal; production never replaces it.
var groupSignal = func(pgid int, signal syscall.Signal) error {
	return syscall.Kill(-pgid, signal)
}

func configureProcessGroup(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

// requestSessionContainment asks the Engine binary to run as its own session
// starter. It must be called after command.Env has been set. An Engine without
// the mode ignores the variable and writes no acknowledgement.
func requestSessionContainment(command *exec.Cmd) {
	command.Env = append(command.Env, sessionContainmentEnv+"=1")
}

// terminateProcess ends the process group the command leads.
//
// Ownership rule: a process group is addressed by a number, and that number
// is ours only while the leader has not been reaped — running or exited, an
// unreaped leader pins it. Every signal here is therefore sent before the
// command is waited for, and callers must not have a waiter running: both the
// agent and the one-shot client call Wait only after this function returned.
// A command that has already been reaped is not signalled at all, and nothing
// in this package signals the group after the reap, not even to confirm.
func terminateProcess(command *exec.Cmd) error {
	if command == nil || command.Process == nil {
		return nil
	}
	if command.ProcessState != nil {
		// Reaped: the number may belong to anyone now.
		return nil
	}
	pid := command.Process.Pid
	var groupErr error
	if pid > 0 {
		groupErr = groupSignal(pid, syscall.SIGKILL)
		// A member that was in the middle of a fork when the group was
		// signalled can leave a child the signal did not reach. The leader is
		// still unreaped (see above), so the same group is signalled again,
		// briefly.
		for attempt := 0; attempt < groupKillRepeats && groupErr == nil; attempt++ {
			time.Sleep(groupKillPause)
			if groupSignal(pid, syscall.SIGKILL) != nil {
				break
			}
		}
		// Darwin reports EPERM for a group whose only member is an exited,
		// unreaped leader — the normal state after a graceful Engine exit.
		// That is decided by looking, before the reap, never by signalling
		// again after it. If the look fails the error stands.
		if errors.Is(groupErr, syscall.EPERM) {
			if live, known := groupHasLiveMembers(pid); known && !live {
				groupErr = nil
			}
		} else if groupErr == nil {
			// Confirm, still before the reap, that the group is empty; signal
			// again while it is not. If the process table cannot be read the
			// result stays unconfirmed and is not an error.
			confirmed := time.Now().Add(groupConfirmBudget)
			for {
				live, known := groupHasLiveMembers(pid)
				if !known || !live {
					break
				}
				if time.Now().After(confirmed) {
					groupErr = errors.New("process group still has live members")
					break
				}
				_ = groupSignal(pid, syscall.SIGKILL)
				time.Sleep(5 * groupKillPause)
			}
		}
	}
	processErr := command.Process.Kill()
	if groupErr != nil && !errors.Is(groupErr, syscall.ESRCH) {
		return fmt.Errorf("process group could not be terminated: %w", groupErr)
	}
	if processErr != nil && !errors.Is(processErr, os.ErrProcessDone) && !errors.Is(processErr, syscall.ESRCH) {
		return fmt.Errorf("process could not be terminated: %w", processErr)
	}
	return nil
}

// groupHasLiveMembers reads the process table and reports whether the group
// has a member that is not a zombie. It only observes. known is false when
// the table could not be read.
func groupHasLiveMembers(pgid int) (live bool, known bool) {
	output, err := exec.Command("ps", "-A", "-o", "pgid=", "-o", "stat=").Output()
	if err != nil {
		return false, false
	}
	for _, line := range strings.Split(string(output), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 {
			continue
		}
		if group, err := strconv.Atoi(fields[0]); err == nil && group == pgid && !strings.HasPrefix(fields[1], "Z") {
			return true, true
		}
	}
	return false, true
}
