package leanctx

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"reflect"
	"sync"
)

// SessionOptions configures a Product lifecycle session.
type SessionOptions struct {
	ProjectRoot string
	SessionID   string
	TaskID      string
	FailOpen    bool
	Engine      EngineClient
}

// CompletionOptions carries explicit host outcome and deterministic usage.
type CompletionOptions struct {
	Outcome HostOutcome
	Usage   map[string]any
}

// ContextSession owns one immutable Product intent and one Engine view.
type ContextSession struct {
	mu           sync.Mutex
	task         string
	projectRoot  string
	sessionID    string
	taskID       string
	failOpen     bool
	engine       EngineClient
	state        SessionState
	plan         *ContextPlan
	view         *ContextView
	receipt      *ContextReceipt
	prepared     bool
	degradations []string
	firstError   error
}

func NewContextSession(task string, options ...SessionOptions) (*ContextSession, error) {
	if len(options) > 1 {
		return nil, NewConfigurationError("at most one SessionOptions value is allowed")
	}
	option := SessionOptions{}
	if len(options) == 1 {
		option = options[0]
	}
	if err := boundedText(task, "task", maxTaskBytes, false); err != nil {
		return nil, err
	}
	if option.SessionID == "" {
		option.SessionID = runtimeID("session")
	}
	if option.TaskID == "" {
		option.TaskID = runtimeID("task")
	}
	if err := boundedText(option.SessionID, "session_id", maxRefBytes, true); err != nil {
		return nil, err
	}
	if err := boundedText(option.TaskID, "task_id", maxRefBytes, true); err != nil {
		return nil, err
	}
	var engine EngineClient = option.Engine
	if engine == nil {
		created, err := NewSubprocessEngineClient()
		if err != nil {
			return nil, err
		}
		engine = created
	}
	return &ContextSession{task: task, projectRoot: option.ProjectRoot, sessionID: option.SessionID, taskID: option.TaskID, failOpen: option.FailOpen, engine: engine, state: SessionStateCreated}, nil
}

func runtimeID(prefix string) string {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return fmt.Sprintf("%s-%x", prefix, raw[:])
	}
	return prefix + "-" + hex.EncodeToString(raw[:])
}

func (s *ContextSession) State() SessionState {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.state
}

func (s *ContextSession) SessionID() string   { s.mu.Lock(); defer s.mu.Unlock(); return s.sessionID }
func (s *ContextSession) TaskID() string      { s.mu.Lock(); defer s.mu.Unlock(); return s.taskID }
func (s *ContextSession) Task() string        { s.mu.Lock(); defer s.mu.Unlock(); return s.task }
func (s *ContextSession) ProjectRoot() string { s.mu.Lock(); defer s.mu.Unlock(); return s.projectRoot }

func (s *ContextSession) CurrentPlan() *ContextPlan {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.plan
}

func (s *ContextSession) View() *ContextView {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.view
}

func (s *ContextSession) Receipt() *ContextReceipt {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.receipt
}

func (s *ContextSession) Degradations() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.degradations...)
}

func (s *ContextSession) Plan(source *ContextSource, options ...ContextPlanOptions) (*ContextPlan, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.planLocked(source, options...)
}

func (s *ContextSession) PlanFor(source *ContextSource, options ...ContextPlanOptions) (*ContextPlan, error) {
	return s.Plan(source, options...)
}

func (s *ContextSession) planLocked(source *ContextSource, options ...ContextPlanOptions) (*ContextPlan, error) {
	if source == nil {
		return nil, NewValidationError("source must be ContextSource")
	}
	if err := s.ensureNotTerminal(); err != nil {
		return nil, err
	}
	candidate, err := NewContextPlan(s.sessionID, s.taskID, s.task, *source, options...)
	if err != nil {
		return nil, err
	}
	if s.plan != nil {
		if s.plan.PlanID != candidate.PlanID {
			return nil, NewSessionStateError("a session cannot replace its Product intent")
		}
		return s.plan, nil
	}
	s.plan = candidate
	s.state = SessionStatePlanned
	return candidate, nil
}

func (s *ContextSession) Prepare(source *ContextSource, options ...ContextPlanOptions) (*ContextView, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.state == SessionStateCompleted || s.state == SessionStateAborted || s.state == SessionStateClosed {
		return nil, NewSessionStateError("prepare is not legal after terminal completion")
	}
	if s.prepared {
		return s.view, nil
	}
	if s.plan == nil {
		if source == nil {
			return nil, NewSessionStateError("prepare requires a source before planning")
		}
		if _, err := s.planLocked(source, options...); err != nil {
			return nil, err
		}
	} else if source != nil {
		if _, err := s.planLocked(source, options...); err != nil {
			return nil, err
		}
	}
	if s.plan == nil {
		return nil, NewSessionStateError("prepare could not establish a plan")
	}
	s.state = SessionStateExecuting
	view, err := s.engine.ContextView(s.plan)
	if err == nil {
		s.view = view
		s.prepared = true
		if view != nil && view.Status == EngineStatusDegraded {
			s.addDegradation("engine:degraded")
		}
		return view, nil
	}
	var unavailable *EngineUnavailable
	var timeout *EngineTimeout
	if s.failOpen && (errors.As(err, &unavailable) || errors.As(err, &timeout)) {
		code := "engine_error"
		if unavailable != nil {
			code = unavailable.Code
		} else if timeout != nil {
			code = timeout.Code
		}
		s.addDegradation("engine:" + code)
		s.prepared = true
		return nil, nil
	}
	s.abortEngineFailure(err)
	return nil, err
}

// PrepareContext adds cancellation for the concrete subprocess adapter while
// preserving the synchronous EngineClient seam for fakes.
func (s *ContextSession) PrepareContext(ctx context.Context, source *ContextSource, options ...ContextPlanOptions) (*ContextView, error) {
	if client, ok := s.engine.(*SubprocessEngineClient); ok {
		return s.prepareWithContext(ctx, client, source, options...)
	}
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	default:
		return s.Prepare(source, options...)
	}
}

func (s *ContextSession) prepareWithContext(ctx context.Context, client *SubprocessEngineClient, source *ContextSource, options ...ContextPlanOptions) (*ContextView, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.state == SessionStateCompleted || s.state == SessionStateAborted || s.state == SessionStateClosed {
		return nil, NewSessionStateError("prepare is not legal after terminal completion")
	}
	if s.prepared {
		return s.view, nil
	}
	if s.plan == nil {
		if source == nil {
			return nil, NewSessionStateError("prepare requires a source before planning")
		}
		if _, err := s.planLocked(source, options...); err != nil {
			return nil, err
		}
	} else if source != nil {
		if _, err := s.planLocked(source, options...); err != nil {
			return nil, err
		}
	}
	s.state = SessionStateExecuting
	view, err := client.ContextViewContext(ctx, s.plan)
	if err == nil {
		s.view, s.prepared = view, true
		return view, nil
	}
	var unavailable *EngineUnavailable
	var timeout *EngineTimeout
	if s.failOpen && (errors.As(err, &unavailable) || errors.As(err, &timeout)) {
		s.addDegradation("engine:" + errCode(err))
		s.prepared = true
		return nil, nil
	}
	s.abortEngineFailure(err)
	return nil, err
}

// Complete accepts an optional CompletionOptions or HostOutcome after the
// opaque host result. Variadic parsing keeps the host result untyped while
// retaining a strongly validated outcome on the wire.
func (s *ContextSession) Complete(hostResult any, arguments ...any) (*ContextReceipt, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	outcome := HostOutcomeUnknown
	var usage map[string]any
	if len(arguments) > 2 {
		return nil, NewValidationError("too many completion arguments")
	}
	for _, argument := range arguments {
		switch value := argument.(type) {
		case CompletionOptions:
			outcome, usage = value.Outcome, value.Usage
		case HostOutcome:
			outcome = value
		case string:
			outcome = HostOutcome(value)
		case map[string]any:
			usage = value
		case nil:
		default:
			return nil, NewValidationError("completion argument has an unsupported type")
		}
	}
	if s.state == SessionStateCompleted {
		if s.receipt == nil || !sameCompletion(*s.receipt, outcome, usage) {
			return nil, NewSessionStateError("conflicting repeated complete")
		}
		return s.receipt, nil
	}
	if s.state == SessionStateAborted || s.state == SessionStateClosed {
		return nil, NewSessionStateError("complete is not legal after abort/close")
	}
	if s.state != SessionStateExecuting {
		return nil, NewSessionStateError("complete requires an executing session")
	}
	if outcome == HostOutcomeAborted || !validOutcome(outcome) {
		return nil, NewValidationError("complete outcome must be an explicit non-aborted host outcome")
	}
	receipt, err := s.makeReceipt(outcome, hostResult, usage, "", nil)
	if err != nil {
		return nil, err
	}
	s.receipt, s.state = receipt, SessionStateCompleted
	return receipt, nil
}

func (s *ContextSession) Abort(err error) (*ContextReceipt, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err == nil {
		return nil, NewValidationError("abort requires an error")
	}
	if s.state == SessionStateAborted {
		if s.receipt == nil {
			return nil, NewSessionStateError("aborted session has no receipt")
		}
		return s.receipt, nil
	}
	if s.state == SessionStateClosed {
		if s.receipt != nil && s.receipt.Outcome == HostOutcomeAborted {
			return s.receipt, nil
		}
		return nil, NewSessionStateError("closed session has no abort receipt")
	}
	if s.state == SessionStateCompleted {
		return nil, NewSessionStateError("cannot abort a completed session")
	}
	typeName := reflect.TypeOf(err).String()
	receipt, receiptErr := s.makeReceipt(HostOutcomeAborted, nil, nil, typeName, err)
	if receiptErr != nil {
		return nil, receiptErr
	}
	s.firstError, s.receipt, s.state = err, receipt, SessionStateAborted
	return receipt, nil
}

func (s *ContextSession) Recover(view ...*ContextView) (*RecoveredSource, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.state != SessionStateExecuting && s.state != SessionStateCompleted && s.state != SessionStateAborted {
		return nil, NewSessionStateError("recover requires an executing or terminal session")
	}
	selected := s.view
	if len(view) > 1 {
		return nil, NewValidationError("at most one recovery view is allowed")
	}
	if len(view) == 1 {
		selected = view[0]
	}
	if selected == nil || s.plan == nil {
		return nil, NewRecoveryUnavailableError("no validated view is available for recovery")
	}
	if selected != s.view {
		if s.view == nil || !sameBinding(selected, s.view) {
			return nil, NewRecoveryUnavailableError("recovery view is not bound to this session")
		}
	}
	if selected.RecoveryRef == "" {
		return nil, NewRecoveryUnavailableError("view has no recovery binding")
	}
	result, err := s.engine.Recover(s.plan.Source.ProjectRoot, mustRelative(s.plan.Source), selected.RecoveryRef, selected.SourceRef, selected.SourceDigest)
	if err != nil {
		return nil, err
	}
	if result == nil || result.RecoveryRef != selected.RecoveryRef || result.SourceRef != selected.SourceRef || result.SourceDigest != selected.SourceDigest {
		return nil, NewArtifactIntegrityError("recovery binding differs from the validated view", nil, selected)
	}
	return result, nil
}

func mustRelative(source ContextSource) string {
	value, _ := source.RelativePath()
	return value
}

func sameBinding(left, right *ContextView) bool {
	return left != nil && right != nil && left.RecoveryRef == right.RecoveryRef && left.SourceRef == right.SourceRef && left.SourceDigest == right.SourceDigest
}

func (s *ContextSession) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.state == SessionStateClosed {
		return nil
	}
	if s.state != SessionStateCompleted && s.state != SessionStateAborted {
		return NewSessionStateError("close requires a terminal receipt")
	}
	s.state = SessionStateClosed
	return nil
}

func (s *ContextSession) ensureNotTerminal() error {
	if s.state == SessionStateCompleted || s.state == SessionStateAborted || s.state == SessionStateClosed {
		return NewSessionStateError("planning is not legal after terminal completion")
	}
	return nil
}

func (s *ContextSession) addDegradation(value string) {
	for _, existing := range s.degradations {
		if existing == value {
			return
		}
	}
	s.degradations = append(s.degradations, value)
}

func (s *ContextSession) abortEngineFailure(err error) {
	s.firstError = err
	var view *ContextView
	switch value := err.(type) {
	case *EngineRejected:
		view = value.View
	case *PolicyAdmissionError:
		view = value.View
	case *EngineExecutionError:
		view = value.View
	case *SourceUnavailableError:
		view = value.View
	case *ArtifactIntegrityError:
		view = value.View
	}
	if view != nil {
		s.view = view
	}
	s.addDegradation("engine:" + errCode(err))
	receipt, receiptErr := s.makeReceipt(HostOutcomeAborted, nil, nil, "", nil)
	if receiptErr == nil {
		s.receipt = receipt
	}
	s.state = SessionStateAborted
}

func errCode(err error) string {
	if err == nil {
		return "engine_error"
	}
	switch value := err.(type) {
	case *EngineUnavailable:
		return value.Code
	case *EngineTimeout:
		return value.Code
	case *EngineCrashed:
		return value.Code
	case *AgentPermissionError:
		return value.Code
	case *UnsupportedCapabilityError:
		return value.Code
	case *EngineProtocolError:
		return value.Code
	case *CompatibilityError:
		return value.Code
	case *UnsupportedEngineError:
		return value.Code
	case *EngineRejected:
		return value.Code
	case *PolicyAdmissionError:
		return value.Code
	case *EngineExecutionError:
		return value.Code
	case *SourceUnavailableError:
		return value.Code
	case *ArtifactIntegrityError:
		return value.Code
	default:
		return "engine_error"
	}
}

func (s *ContextSession) makeReceipt(outcome HostOutcome, hostResult any, usage map[string]any, hostExceptionType string, hostException error) (*ContextReceipt, error) {
	integrity := IntegrityUnsealed
	if s.view != nil {
		integrity = s.view.IntegrityStatus()
	}
	return NewContextReceipt(s.sessionID, s.taskID, planID(s.plan), s.view, outcome, integrity, ReceiptOptions{Degradations: s.degradations, Usage: usage, HostExceptionType: hostExceptionType, HostResult: hostResult, HostException: hostException})
}

func planID(plan *ContextPlan) *string {
	if plan == nil {
		return nil
	}
	value := plan.PlanID
	return &value
}

func sameCompletion(receipt ContextReceipt, outcome HostOutcome, usage map[string]any) bool {
	if receipt.Outcome != outcome {
		return false
	}
	left, errLeft := canonicalJSON(receipt.Usage)
	right, errRight := canonicalJSON(usage)
	if receipt.Usage == nil && usage == nil {
		return true
	}
	return errLeft == nil && errRight == nil && bytes.Equal(left, right)
}
