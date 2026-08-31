package leanctx

import (
	"encoding/json"
	"reflect"
	"strings"
)

// ReceiptOptions contains optional host-owned completion evidence. HostResult
// and HostException are intentionally not included in ToDict.
type ReceiptOptions struct {
	Degradations      []string
	Usage             map[string]any
	HostExceptionType string
	HostResult        any
	HostException     error
}

// ContextReceipt is the immutable host completion projection.
type ContextReceipt struct {
	SessionID         string
	TaskID            string
	PlanID            *string
	View              *ContextView
	Outcome           HostOutcome
	IntegrityStatus   Integrity
	Degradations      []string
	Usage             map[string]any
	HostExceptionType string
	HostResult        any
	HostException     error
	SchemaVersion     int
}

// NewContextReceipt validates and copies a host completion receipt.
func NewContextReceipt(sessionID, taskID string, planID *string, view *ContextView, outcome HostOutcome, integrity Integrity, options ...ReceiptOptions) (*ContextReceipt, error) {
	if len(options) > 1 {
		return nil, NewValidationError("at most one ReceiptOptions value is allowed")
	}
	option := ReceiptOptions{}
	if len(options) == 1 {
		option = options[0]
	}
	if err := boundedText(sessionID, "session_id", maxRefBytes, true); err != nil {
		return nil, err
	}
	if err := boundedText(taskID, "task_id", maxRefBytes, true); err != nil {
		return nil, err
	}
	if planID != nil {
		if err := planRef(*planID); err != nil {
			return nil, err
		}
	}
	if view != nil {
		copyView := *view
		view = &copyView
	}
	if !validOutcome(outcome) {
		return nil, NewValidationError("invalid host outcome")
	}
	if integrity != IntegritySealed && integrity != IntegrityUnsealed {
		return nil, NewValidationError("invalid integrity status")
	}
	if option.HostExceptionType != "" {
		if len([]byte(option.HostExceptionType)) > maxRefBytes || strings.ContainsAny(option.HostExceptionType, ":\n") {
			return nil, NewValidationError("host_exception_type must be a safe type name")
		}
	}
	if option.HostException != nil && outcome != HostOutcomeAborted {
		return nil, NewValidationError("host_exception requires an aborted outcome")
	}
	if option.HostException != nil && option.HostExceptionType != "" {
		expected := reflect.TypeOf(option.HostException)
		if expected != nil && option.HostExceptionType != expected.String() && option.HostExceptionType != expected.Name() {
			return nil, NewValidationError("host_exception_type does not match host_exception")
		}
	}
	usage, err := cloneMap(option.Usage)
	if err != nil {
		return nil, NewValidationError("usage must be deterministic JSON data")
	}
	degradations := append([]string{}, option.Degradations...)
	for _, degradation := range degradations {
		if degradation == "" {
			return nil, NewValidationError("degradations must be non-empty strings")
		}
	}
	if integrity == IntegritySealed && (view == nil || !view.Verify()) {
		return nil, NewValidationError("sealed receipt requires verified Engine evidence")
	}
	planCopy := cloneStringPointer(planID)
	return &ContextReceipt{SessionID: sessionID, TaskID: taskID, PlanID: planCopy, View: view, Outcome: outcome, IntegrityStatus: integrity, Degradations: degradations, Usage: usage, HostExceptionType: option.HostExceptionType, HostResult: option.HostResult, HostException: option.HostException, SchemaVersion: SchemaVersion}, nil
}

func (r ContextReceipt) Sealed() bool { return r.IntegrityStatus == IntegritySealed }

func (r ContextReceipt) Status() *EngineStatus {
	if r.View == nil {
		return nil
	}
	status := r.View.Status
	return &status
}

func (r ContextReceipt) Source() *ContextSource {
	if r.View == nil {
		return nil
	}
	source := r.View.Source
	return &source
}

func (r ContextReceipt) Invocation() map[string]any {
	if r.View == nil {
		return nil
	}
	copy, _ := cloneMap(r.View.Invocation)
	return copy
}

func (r ContextReceipt) Observation() map[string]any {
	if r.View == nil {
		return nil
	}
	copy, _ := cloneMap(r.View.Observation)
	return copy
}

func (r ContextReceipt) ReceiptLink() *ContextReceiptLink {
	if r.View == nil || r.View.ReceiptLink == nil {
		return nil
	}
	link := *r.View.ReceiptLink
	return &link
}

func (r ContextReceipt) RecoveryRef() string {
	if r.View == nil {
		return ""
	}
	return r.View.RecoveryRef
}

func (r ContextReceipt) OutputDigest() string {
	if r.View == nil || r.View.OutputDigest == nil {
		return ""
	}
	return *r.View.OutputDigest
}

func (r ContextReceipt) Verify() bool {
	return r.Sealed() && r.View != nil && r.View.Verify()
}

func (r ContextReceipt) RequireVerified() error {
	if !r.Verify() {
		return NewArtifactIntegrityError("receipt evidence is not sealed", nil, r.View)
	}
	return nil
}

// ToDict intentionally omits opaque host result and exception values.
func (r ContextReceipt) ToDict() map[string]any {
	var view map[string]any
	if r.View != nil {
		view = r.View.ToDict()
	}
	var plan any
	if r.PlanID != nil {
		plan = *r.PlanID
	}
	var source any
	var invocation any
	var observation any
	var link any
	var status any
	if view != nil {
		source = view["source"]
		invocation = view["invocation"]
		observation = view["observation"]
		link = view["receipt_link"]
		status = view["status"]
	}
	var usage any
	if r.Usage != nil {
		usage, _ = cloneMap(r.Usage)
	}
	var exceptionType any
	if r.HostExceptionType != "" {
		exceptionType = r.HostExceptionType
	}
	degradations := append([]string{}, r.Degradations...)
	return map[string]any{"schema_version": int64(r.SchemaVersion), "session_id": r.SessionID, "task_id": r.TaskID, "plan_id": plan, "outcome": string(r.Outcome), "integrity_status": string(r.IntegrityStatus), "degradations": degradations, "usage": usage, "host_exception_type": exceptionType, "status": status, "source": source, "invocation": invocation, "observation": observation, "receipt_link": link, "recovery_ref": nullableString(r.RecoveryRef()), "output_digest": nullableString(r.OutputDigest())}
}

func (r ContextReceipt) MarshalJSON() ([]byte, error) { return json.Marshal(r.ToDict()) }
