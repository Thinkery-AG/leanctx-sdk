package leanctx

// Process-tree tests that run on every desktop platform, Windows included.
// The stand-in Engine is this test binary itself: when it is started the way
// the SDK starts an Engine (`engine tool-session …`) TestMain plays the Engine
// instead of running tests. No shell and no LeanCTX instance is involved.

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestMain(m *testing.M) {
	if len(os.Args) >= 3 && os.Args[1] == "engine" {
		switch os.Args[2] {
		case "tool-session":
			selfExecInstance()
		case "tool-session-descendant":
			time.Sleep(60 * time.Second)
		}
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func selfExecPublish(name string, pid int) {
	_ = os.WriteFile(name+".tmp", []byte(strconv.Itoa(pid)), 0o600)
	_ = os.Rename(name+".tmp", name)
}

// selfExecInstance answers the hello, starts one descendant that shares none of
// its pipes, and then follows the mode the test wrote into the project root.
func selfExecInstance() {
	mode, _ := os.ReadFile("selfexec-mode.txt")
	line, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if err != nil {
		os.Exit(21)
	}
	var request map[string]any
	if err := json.Unmarshal([]byte(line), &request); err != nil {
		os.Exit(22)
	}
	descendant := exec.Command(os.Args[0], "engine", "tool-session-descendant")
	if err := descendant.Start(); err != nil {
		os.Exit(23)
	}
	selfExecPublish("descendant.pid", descendant.Process.Pid)
	selfExecPublish("leader.pid", os.Getpid())
	hello, _ := json.Marshal(map[string]any{"id": request["id"], "ok": true, "result": map[string]any{
		"agent_tools_interface_version": "1.0.0", "allow_exec": false, "allow_write": false,
		"capabilities":   []string{"ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"},
		"engine_version": "3.11.0", "schema_version": 1, "transport_version": 1,
	}})
	fmt.Println(string(hello))
	if strings.TrimSpace(string(mode)) == "exit-after-hello" {
		os.Exit(0)
	}
	time.Sleep(60 * time.Second)
}

// selfExecAlive reads the process table; a zombie does not count as alive.
func selfExecAlive(pid int) bool {
	if runtime.GOOS == "windows" {
		output, _ := exec.Command("tasklist", "/FI", "PID eq "+strconv.Itoa(pid), "/NH").Output()
		return strings.Contains(string(output), " "+strconv.Itoa(pid)+" ")
	}
	output, err := exec.Command("ps", "-o", "stat=", "-p", strconv.Itoa(pid)).Output()
	state := strings.TrimSpace(string(output))
	return err == nil && state != "" && !strings.HasPrefix(state, "Z")
}

func selfExecOpen(t *testing.T, mode string) (*AgentContext, int, int) {
	t.Helper()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "selfexec-mode.txt"), []byte(mode), 0o600); err != nil {
		t.Fatal(err)
	}
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	client, err := OpenAgentContext(context.Background(), root, AgentContextOptions{InstanceBinary: binary, Timeout: 30 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	pid := func(name string) int {
		for attempt := 0; attempt < 400; attempt++ {
			if raw, err := os.ReadFile(filepath.Join(client.ProjectRoot, name)); err == nil {
				if value, err := strconv.Atoi(strings.TrimSpace(string(raw))); err == nil {
					return value
				}
			}
			time.Sleep(5 * time.Millisecond)
		}
		t.Fatalf("stand-in instance did not publish %s", name)
		return 0
	}
	leader, descendant := pid("leader.pid"), pid("descendant.pid")
	t.Cleanup(func() {
		_ = client.Cancel()
		// The descendant is this test's own grandchild; end it if the SDK did not.
		if selfExecAlive(descendant) {
			if process, err := os.FindProcess(descendant); err == nil {
				_ = process.Kill()
			}
		}
	})
	return client, leader, descendant
}

func selfExecRequireGone(t *testing.T, what string, pid int) {
	t.Helper()
	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		if !selfExecAlive(pid) {
			return
		}
		time.Sleep(25 * time.Millisecond)
	}
	t.Fatalf("%s (pid %d) survived", what, pid)
}

// Cancel while a call is pending ends the Engine and its live descendant.
func TestSelfExecCancelEndsLeaderAndDescendant(t *testing.T) {
	client, leader, descendant := selfExecOpen(t, "hang")
	if !selfExecAlive(leader) || !selfExecAlive(descendant) {
		t.Fatalf("stand-in tree is not running: leader=%v descendant=%v", selfExecAlive(leader), selfExecAlive(descendant))
	}
	callErr := make(chan error, 1)
	go func() {
		_, err := client.Call("ctx_read", map[string]any{})
		callErr <- err
	}()
	time.Sleep(300 * time.Millisecond)
	started := time.Now()
	if err := client.Cancel(); err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed > 8*time.Second {
		t.Fatalf("Cancel returned after %s", elapsed)
	}
	select {
	case err := <-callErr:
		var crashed *InstanceCrashed
		if !errors.As(err, &crashed) {
			t.Fatalf("cancelled call = %T: %v", err, err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("cancelled call is still blocked")
	}
	selfExecRequireGone(t, "LeanCTX instance leader", leader)
	selfExecRequireGone(t, "LeanCTX instance descendant", descendant)
}

// The Engine exits by itself and leaves a descendant behind. The next call is
// the typed terminal error and the descendant is ended with the session.
//
// POSIX: the descendant is a member of the Engine's process group and is
// killed. Windows: KNOWN OPEN — a descendant whose parent already exited is
// not reachable through `taskkill /T`; this test fails there until the Engine
// is started inside a Job Object. It is not skipped, so the gap stays visible.
func TestSelfExecOrphanedDescendantIsEndedAfterLeaderExit(t *testing.T) {
	client, leader, descendant := selfExecOpen(t, "exit-after-hello")
	selfExecRequireGone(t, "LeanCTX instance leader", leader)
	var crashed *InstanceCrashed
	if _, err := client.Read("file"); !errors.As(err, &crashed) {
		t.Fatalf("call after instance exit = %T: %v", err, err)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("Close after instance exit = %v", err)
	}
	selfExecRequireGone(t, "orphaned instance descendant", descendant)
}
