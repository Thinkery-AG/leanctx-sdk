package leanctx

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

// EngineClient is the only injected transport seam for Product lifecycle
// tests and integrations. Context cancellation is available on the concrete
// SubprocessEngineClient through its Context*Context methods.
type EngineClient interface {
	ContextView(plan *ContextPlan) (*ContextView, error)
	Recover(projectRoot, path, recoveryRef, sourceRef, sourceDigest string) (*RecoveredSource, error)
}

type parsedView struct {
	Text         string
	OutputRef    *string
	OutputDigest *string
}

type parsedRecovery struct {
	RecoveryRef  string
	SourceRef    string
	SourceDigest string
}

type parsedResponse struct {
	View        parsedView
	Invocation  map[string]any
	Observation map[string]any
	HasRecords  bool
	Recovery    parsedRecovery
}

var (
	engineTopKeys = map[string]bool{
		"schema_version": true, "transport_version": true,
		"engine_interface_version": true, "view": true,
		"invocation": true, "observation": true, "recovery": true,
	}
	viewKeys       = map[string]bool{"text": true, "output_ref": true, "output_digest": true}
	recoveryKeys   = map[string]bool{"recovery_ref": true, "source_ref": true, "source_digest": true}
	invocationKeys = map[string]bool{
		"schema_version": true, "invocation_id": true, "engine": true,
		"operation": true, "input_ref": true, "input_digest": true,
		"source_refs": true, "policy_admission": true,
	}
	engineIdentityKeys = map[string]bool{"engine_id": true, "engine_version": true}
	operationKeys      = map[string]bool{"capability_id": true, "capability_version": true}
	policyKeys         = map[string]bool{"policy_ref": true, "decision": true}
	observationKeys    = map[string]bool{
		"schema_version": true, "invocation_id": true, "status": true,
		"output_ref": true, "output_digest": true, "source_lineage": true,
		"measurements": true, "failure": true, "receipt_link": true,
	}
	measurementKeys = map[string]bool{"name": true, "unit": true, "classification": true, "value": true}
	failureKeys     = map[string]bool{"code": true, "retryable_by_host": true, "recovery_ref": true}
	receiptLinkKeys = map[string]bool{"schema_version": true, "receipt_id": true, "receipt_ref": true, "receipt_digest": true, "invocation_id": true}
)

func protocolError(message string) error { return NewEngineProtocolError(message) }

func objectValue(value any, label string) (map[string]any, error) {
	result, ok := value.(map[string]any)
	if !ok {
		return nil, protocolError(label + " must be an object")
	}
	return result, nil
}

func exactObjectKeys(value map[string]any, expected map[string]bool, label string) error {
	if len(value) != len(expected) {
		return protocolError(label + " fields do not match the v1 contract")
	}
	for key := range value {
		if !expected[key] {
			return protocolError(label + " fields do not match the v1 contract")
		}
	}
	return nil
}

func allowedObjectKeys(value map[string]any, allowed, required map[string]bool, label string) error {
	for key := range value {
		if !allowed[key] {
			return protocolError(label + " fields do not match the v1 contract")
		}
	}
	for key := range required {
		if _, ok := value[key]; !ok {
			return protocolError(label + " fields do not match the v1 contract")
		}
	}
	return nil
}

func protocolInt(value any, field string) (int64, error) {
	switch item := value.(type) {
	case json.Number:
		result, err := normalizeNumber(item.String())
		if err != nil {
			return 0, protocolError(field + " must be an integer")
		}
		return result, nil
	case int64:
		if item < -maxSafeInteger || item > maxSafeInteger {
			return 0, protocolError(field + " must be an integer")
		}
		return item, nil
	case int:
		return protocolInt(int64(item), field)
	default:
		return 0, protocolError(field + " must be an integer")
	}
}

func protocolString(value any, field string, maximum int) (string, error) {
	item, ok := value.(string)
	if !ok {
		return "", protocolError(field + " must be a string")
	}
	if len([]byte(item)) > maximum || strings.IndexByte(item, 0) >= 0 {
		return "", protocolError(field + " violates its bound")
	}
	return item, nil
}

func protocolRef(value any, field string) (string, error) {
	item, err := protocolString(value, field, maxRefBytes)
	if err != nil {
		return "", err
	}
	if err := validateRef(item, field); err != nil {
		return "", protocolError(err.Error())
	}
	return item, nil
}

func optionalDigest(value any, field string) (*string, error) {
	if value == nil {
		return nil, nil
	}
	item, err := protocolString(value, field, maxRefBytes)
	if err != nil {
		return nil, err
	}
	if err := validateDigest(item, field); err != nil {
		return nil, protocolError(err.Error())
	}
	return &item, nil
}

func optionalOutputRef(value any, field string) (*string, error) {
	if value == nil {
		return nil, nil
	}
	item, err := protocolString(value, field, maxRefBytes)
	if err != nil {
		return nil, err
	}
	if err := validateOutputRef(item, field); err != nil {
		return nil, protocolError(err.Error())
	}
	return &item, nil
}

func requiredDigest(value any, field string) (string, error) {
	item, err := protocolString(value, field, maxRefBytes)
	if err != nil {
		return "", err
	}
	if err := validateDigest(item, field); err != nil {
		return "", protocolError(err.Error())
	}
	return item, nil
}

func validateOutputPair(ref, digest *string, label string) error {
	if ref != nil && digest == nil {
		return protocolError(label + " output reference requires a digest")
	}
	if ref != nil && digest != nil && *ref != "output:"+strings.TrimPrefix(*digest, "sha256:") {
		return protocolError(label + " output reference does not match digest")
	}
	return nil
}

func parseMeasurement(value any) (ContextMeasurement, error) {
	item, err := objectValue(value, "measurement")
	if err != nil {
		return ContextMeasurement{}, err
	}
	if err := exactObjectKeys(item, measurementKeys, "measurement"); err != nil {
		return ContextMeasurement{}, err
	}
	name, err := protocolString(item["name"], "measurement.name", maxRefBytes)
	if err != nil {
		return ContextMeasurement{}, err
	}
	unit, err := protocolString(item["unit"], "measurement.unit", maxRefBytes)
	if err != nil {
		return ContextMeasurement{}, err
	}
	classification, err := protocolString(item["classification"], "measurement.classification", maxRefBytes)
	if err != nil {
		return ContextMeasurement{}, err
	}
	var valuePtr *int64
	if item["value"] != nil {
		parsed, err := protocolInt(item["value"], "measurement.value")
		if err != nil {
			return ContextMeasurement{}, err
		}
		valuePtr = &parsed
	}
	measurement, err := NewContextMeasurement(name, unit, classification, valuePtr)
	if err != nil {
		return ContextMeasurement{}, protocolError(err.Error())
	}
	return *measurement, nil
}

func parseFailure(value any) (*ContextFailure, error) {
	if value == nil {
		return nil, nil
	}
	item, err := objectValue(value, "failure")
	if err != nil {
		return nil, err
	}
	if err := exactObjectKeys(item, failureKeys, "failure"); err != nil {
		return nil, err
	}
	codeText, err := protocolString(item["code"], "failure.code", maxRefBytes)
	if err != nil {
		return nil, err
	}
	retryable, ok := item["retryable_by_host"].(bool)
	if !ok {
		return nil, protocolError("failure.retryable_by_host must be boolean")
	}
	recovery := ""
	if item["recovery_ref"] != nil {
		recovery, err = protocolRef(item["recovery_ref"], "failure.recovery_ref")
		if err != nil {
			return nil, err
		}
	}
	failure, err := NewContextFailure(FailureCode(codeText), retryable, recovery)
	if err != nil {
		return nil, protocolError("unknown Engine failure code")
	}
	return failure, nil
}

func parseReceiptLink(value any, invocationID string) (*ContextReceiptLink, error) {
	if value == nil {
		return nil, nil
	}
	item, err := objectValue(value, "receipt_link")
	if err != nil {
		return nil, err
	}
	if err := exactObjectKeys(item, receiptLinkKeys, "receipt_link"); err != nil {
		return nil, err
	}
	schema, err := protocolInt(item["schema_version"], "receipt_link.schema_version")
	if err != nil {
		return nil, err
	}
	receiptID, err := protocolRef(item["receipt_id"], "receipt_link.receipt_id")
	if err != nil {
		return nil, err
	}
	receiptRef, err := protocolRef(item["receipt_ref"], "receipt_link.receipt_ref")
	if err != nil {
		return nil, err
	}
	receiptDigest, err := requiredDigest(item["receipt_digest"], "receipt_link.receipt_digest")
	if err != nil {
		return nil, err
	}
	observedID, err := protocolString(item["invocation_id"], "receipt_link.invocation_id", maxRefBytes)
	if err != nil {
		return nil, err
	}
	if receiptRef != "receipt:"+receiptDigest {
		return nil, protocolError("receipt_link.receipt_ref does not match digest")
	}
	if observedID != invocationID {
		return nil, protocolError("receipt_link invocation binding mismatch")
	}
	link, err := NewContextReceiptLink(int(schema), receiptID, receiptRef, receiptDigest, observedID)
	if err != nil {
		return nil, protocolError(err.Error())
	}
	return link, nil
}

func parseInvocation(value any) (map[string]any, error) {
	item, err := objectValue(value, "invocation")
	if err != nil {
		return nil, err
	}
	if err := exactObjectKeys(item, invocationKeys, "invocation"); err != nil {
		return nil, err
	}
	if _, err := protocolInt(item["schema_version"], "invocation.schema_version"); err != nil {
		return nil, err
	}
	invocationID, err := protocolString(item["invocation_id"], "invocation.invocation_id", maxRefBytes)
	if err != nil {
		return nil, err
	}
	engine, err := objectValue(item["engine"], "invocation.engine")
	if err != nil {
		return nil, err
	}
	operation, err := objectValue(item["operation"], "invocation.operation")
	if err != nil {
		return nil, err
	}
	policy, err := objectValue(item["policy_admission"], "invocation.policy_admission")
	if err != nil {
		return nil, err
	}
	if err := exactObjectKeys(engine, engineIdentityKeys, "invocation.engine"); err != nil {
		return nil, err
	}
	if err := exactObjectKeys(operation, operationKeys, "invocation.operation"); err != nil {
		return nil, err
	}
	if err := exactObjectKeys(policy, policyKeys, "invocation.policy_admission"); err != nil {
		return nil, err
	}
	engineID, err := protocolString(engine["engine_id"], "invocation.engine.engine_id", maxRefBytes)
	if err != nil {
		return nil, err
	}
	engineVersion, err := protocolString(engine["engine_version"], "invocation.engine.engine_version", maxRefBytes)
	if err != nil {
		return nil, err
	}
	if engineID != "lean-ctx-local" || !semverPattern.MatchString(engineVersion) || !strings.HasPrefix(engineVersion, "3.") {
		return nil, NewUnsupportedEngineError("unsupported Engine identity")
	}
	capabilityID, err := protocolString(operation["capability_id"], "invocation.operation.capability_id", maxRefBytes)
	if err != nil {
		return nil, err
	}
	capabilityVersion, err := protocolString(operation["capability_version"], "invocation.operation.capability_version", maxRefBytes)
	if err != nil {
		return nil, err
	}
	if capabilityID != "capability://leanctx/context-optimization" || capabilityVersion != "1.0.0" {
		return nil, NewUnsupportedEngineError("unsupported Engine capability")
	}
	decision, err := protocolString(policy["decision"], "invocation.policy_admission.decision", maxRefBytes)
	if err != nil {
		return nil, err
	}
	if decision != "admitted" && decision != "rejected" {
		return nil, protocolError("unknown policy decision")
	}
	policyRef, err := protocolRef(policy["policy_ref"], "invocation.policy_admission.policy_ref")
	if err != nil {
		return nil, err
	}
	inputRef, err := protocolRef(item["input_ref"], "invocation.input_ref")
	if err != nil {
		return nil, err
	}
	inputDigest, err := requiredDigest(item["input_digest"], "invocation.input_digest")
	if err != nil {
		return nil, err
	}
	refsValue, ok := item["source_refs"].([]any)
	if !ok || len(refsValue) == 0 || len(refsValue) > maxRefs {
		return nil, protocolError("invocation.source_refs exceeds its bound")
	}
	refs := make([]string, len(refsValue))
	seen := make(map[string]bool, len(refsValue))
	for i, entry := range refsValue {
		refs[i], err = protocolRef(entry, "invocation.source_refs")
		if err != nil {
			return nil, err
		}
		if seen[refs[i]] {
			return nil, protocolError("invocation.source_refs contains duplicates")
		}
		seen[refs[i]] = true
	}
	if !seen[inputRef] {
		return nil, protocolError("invocation input_ref is not in source_refs")
	}
	return map[string]any{"schema_version": int64(SchemaVersion), "invocation_id": invocationID, "engine": map[string]any{"engine_id": engineID, "engine_version": engineVersion}, "operation": map[string]any{"capability_id": capabilityID, "capability_version": capabilityVersion}, "input_ref": inputRef, "input_digest": inputDigest, "source_refs": refs, "policy_admission": map[string]any{"policy_ref": policyRef, "decision": decision}}, nil
}

func parseObservation(value any, invocationID string) (map[string]any, error) {
	item, err := objectValue(value, "observation")
	if err != nil {
		return nil, err
	}
	required := map[string]bool{"schema_version": true, "invocation_id": true, "status": true, "source_lineage": true, "measurements": true}
	if err := allowedObjectKeys(item, observationKeys, required, "observation"); err != nil {
		return nil, err
	}
	schema, err := protocolInt(item["schema_version"], "observation.schema_version")
	if err != nil || schema != SchemaVersion {
		if err != nil {
			return nil, err
		}
		return nil, protocolError("unsupported observation schema version")
	}
	observedID, err := protocolString(item["invocation_id"], "observation.invocation_id", maxRefBytes)
	if err != nil {
		return nil, err
	}
	if observedID != invocationID {
		return nil, protocolError("observation invocation binding mismatch")
	}
	statusText, err := protocolString(item["status"], "observation.status", maxRefBytes)
	if err != nil {
		return nil, err
	}
	status := EngineStatus(statusText)
	if !validStatus(status) {
		return nil, protocolError("unknown observation status")
	}
	outputRef, err := optionalOutputRef(item["output_ref"], "observation.output_ref")
	if err != nil {
		return nil, err
	}
	outputDigest, err := optionalDigest(item["output_digest"], "observation.output_digest")
	if err != nil {
		return nil, err
	}
	if err := validateOutputPair(outputRef, outputDigest, "observation"); err != nil {
		return nil, err
	}
	lineageValue, ok := item["source_lineage"].([]any)
	if !ok || len(lineageValue) == 0 || len(lineageValue) > maxRefs {
		return nil, protocolError("observation.source_lineage exceeds its bound")
	}
	lineage := make([]string, len(lineageValue))
	seen := make(map[string]bool, len(lineageValue))
	for i, entry := range lineageValue {
		lineage[i], err = protocolRef(entry, "observation.source_lineage")
		if err != nil {
			return nil, err
		}
		if seen[lineage[i]] {
			return nil, protocolError("observation.source_lineage contains duplicates")
		}
		seen[lineage[i]] = true
	}
	measurementsValue, ok := item["measurements"].([]any)
	if !ok || len(measurementsValue) > maxMeasurements {
		return nil, protocolError("observation.measurements exceeds its bound")
	}
	measurements := make([]ContextMeasurement, len(measurementsValue))
	for i, entry := range measurementsValue {
		measurements[i], err = parseMeasurement(entry)
		if err != nil {
			return nil, err
		}
	}
	failure, err := parseFailure(item["failure"])
	if err != nil {
		return nil, err
	}
	link, err := parseReceiptLink(item["receipt_link"], invocationID)
	if err != nil {
		return nil, err
	}
	if (status == EngineStatusSucceeded || status == EngineStatusDegraded) && failure != nil {
		return nil, protocolError("successful/degraded observation cannot contain failure")
	}
	if (status == EngineStatusFailed || status == EngineStatusRejected) && failure == nil {
		return nil, protocolError("failed/rejected observation requires failure")
	}
	if status == EngineStatusSucceeded && link == nil {
		return nil, protocolError("succeeded observation requires receipt_link")
	}
	result := map[string]any{"schema_version": int64(SchemaVersion), "invocation_id": observedID, "status": statusText, "output_ref": pointerOrNil(outputRef), "output_digest": pointerOrNil(outputDigest), "source_lineage": lineage, "measurements": measurements, "failure": failure, "receipt_link": link}
	return result, nil
}

func parseView(value any) (parsedView, error) {
	item, err := objectValue(value, "view")
	if err != nil {
		return parsedView{}, err
	}
	if err := exactObjectKeys(item, viewKeys, "view"); err != nil {
		return parsedView{}, err
	}
	text, err := protocolString(item["text"], "view.text", maxTextBytes)
	if err != nil {
		return parsedView{}, err
	}
	outputRef, err := optionalOutputRef(item["output_ref"], "view.output_ref")
	if err != nil {
		return parsedView{}, err
	}
	outputDigest, err := optionalDigest(item["output_digest"], "view.output_digest")
	if err != nil {
		return parsedView{}, err
	}
	if err := validateOutputPair(outputRef, outputDigest, "view"); err != nil {
		return parsedView{}, err
	}
	if outputDigest != nil && sha256Hex([]byte(text)) != *outputDigest {
		return parsedView{}, protocolError("view output digest mismatch")
	}
	return parsedView{Text: text, OutputRef: outputRef, OutputDigest: outputDigest}, nil
}

func parseRecovery(value any) (parsedRecovery, error) {
	item, err := objectValue(value, "recovery")
	if err != nil {
		return parsedRecovery{}, err
	}
	if err := exactObjectKeys(item, recoveryKeys, "recovery"); err != nil {
		return parsedRecovery{}, err
	}
	recoveryRef, err := protocolRef(item["recovery_ref"], "recovery.recovery_ref")
	if err != nil {
		return parsedRecovery{}, err
	}
	sourceRef, err := protocolRef(item["source_ref"], "recovery.source_ref")
	if err != nil {
		return parsedRecovery{}, err
	}
	sourceDigest, err := requiredDigest(item["source_digest"], "recovery.source_digest")
	if err != nil {
		return parsedRecovery{}, err
	}
	return parsedRecovery{RecoveryRef: recoveryRef, SourceRef: sourceRef, SourceDigest: sourceDigest}, nil
}

func parseResponse(raw []byte) (parsedResponse, error) {
	if len(raw) > maxResponseBytes {
		return parsedResponse{}, protocolError("Engine response exceeds the bound")
	}
	decoded, err := strictJSONLoads(raw, "Engine response")
	if err != nil {
		return parsedResponse{}, protocolError(err.Error())
	}
	item, err := objectValue(decoded, "Engine response")
	if err != nil {
		return parsedResponse{}, err
	}
	if err := exactObjectKeys(item, engineTopKeys, "Engine response"); err != nil {
		return parsedResponse{}, err
	}
	schema, err := protocolInt(item["schema_version"], "response.schema_version")
	if err != nil {
		return parsedResponse{}, err
	}
	if schema != SchemaVersion {
		return parsedResponse{}, NewCompatibilityError("unsupported schema version")
	}
	transport, err := protocolInt(item["transport_version"], "response.transport_version")
	if err != nil {
		return parsedResponse{}, err
	}
	if transport != TransportVersion {
		return parsedResponse{}, NewCompatibilityError("unsupported transport version")
	}
	interfaceVersion, err := protocolString(item["engine_interface_version"], "response.engine_interface_version", maxRefBytes)
	if err != nil {
		return parsedResponse{}, err
	}
	if interfaceVersion != EngineInterfaceVersion {
		return parsedResponse{}, NewCompatibilityError("unsupported Engine Interface version")
	}
	view, err := parseView(item["view"])
	if err != nil {
		return parsedResponse{}, err
	}
	recovery, err := parseRecovery(item["recovery"])
	if err != nil {
		return parsedResponse{}, err
	}
	if item["invocation"] == nil || item["observation"] == nil {
		if item["invocation"] != nil || item["observation"] != nil {
			return parsedResponse{}, protocolError("invocation and observation must both be null or present")
		}
		return parsedResponse{View: view, Recovery: recovery}, nil
	}
	invocation, err := parseInvocation(item["invocation"])
	if err != nil {
		return parsedResponse{}, err
	}
	invocationID, _ := invocation["invocation_id"].(string)
	observation, err := parseObservation(item["observation"], invocationID)
	if err != nil {
		return parsedResponse{}, err
	}
	lineage, ok := observation["source_lineage"].([]string)
	if !ok {
		return parsedResponse{}, protocolError("observation source lineage is malformed")
	}
	refs, ok := invocation["source_refs"].([]string)
	if !ok || len(lineage) != len(refs) {
		return parsedResponse{}, protocolError("observation source lineage does not match invocation")
	}
	for i := range lineage {
		if lineage[i] != refs[i] {
			return parsedResponse{}, protocolError("observation source lineage does not match invocation")
		}
	}
	if !optionalStringEqual(observation["output_ref"], view.OutputRef) || !optionalStringEqual(observation["output_digest"], view.OutputDigest) {
		return parsedResponse{}, protocolError("view and observation output binding mismatch")
	}
	return parsedResponse{View: view, Invocation: invocation, Observation: observation, HasRecords: true, Recovery: recovery}, nil
}

func optionalStringEqual(value any, expected *string) bool {
	if expected == nil {
		return value == nil
	}
	actual, ok := value.(string)
	return ok && actual == *expected
}

func safeRelativePath(value string) (string, error) {
	if err := utf8String(value, "path"); err != nil {
		return "", NewEngineProtocolError(err.Error())
	}
	if value == "" || len([]byte(value)) > maxPathBytes || strings.IndexByte(value, 0) >= 0 || filepath.IsAbs(value) {
		return "", NewEngineProtocolError("path must be a rooted relative path")
	}
	for _, r := range value {
		if r < 0x20 {
			return "", NewEngineProtocolError("path must be a rooted relative path")
		}
	}
	normalized := filepath.ToSlash(filepath.Clean(value))
	if normalized == "." || normalized == ".." || strings.HasPrefix(normalized, "../") {
		return "", NewEngineProtocolError("path escapes project root")
	}
	return normalized, nil
}

// SubprocessEngineClient implements strict Engine Interface v1.
type SubprocessEngineClient struct {
	EngineBinary string
	Timeout      time.Duration
	mu           sync.Mutex
}

type SubprocessEngineClientOptions struct {
	EngineBinary string
	Timeout      time.Duration
}

func NewSubprocessEngineClient(options ...SubprocessEngineClientOptions) (*SubprocessEngineClient, error) {
	if len(options) > 1 {
		return nil, NewConfigurationError("at most one SubprocessEngineClientOptions value is allowed")
	}
	option := SubprocessEngineClientOptions{EngineBinary: "lean-ctx", Timeout: 30 * time.Second}
	if len(options) == 1 {
		option = options[0]
		if option.EngineBinary == "" {
			option.EngineBinary = "lean-ctx"
		}
		if option.Timeout == 0 {
			option.Timeout = 30 * time.Second
		}
	}
	if option.Timeout < 100*time.Millisecond || option.Timeout > 120*time.Second {
		return nil, NewConfigurationError("timeout must be between 0.1 and 120 seconds")
	}
	return &SubprocessEngineClient{EngineBinary: option.EngineBinary, Timeout: option.Timeout}, nil
}

func (c *SubprocessEngineClient) configured() error {
	if c == nil {
		return NewConfigurationError("SubprocessEngineClient is nil")
	}
	if c.Timeout == 0 {
		c.Timeout = 30 * time.Second
	}
	if c.Timeout < 100*time.Millisecond || c.Timeout > 120*time.Second {
		return NewConfigurationError("timeout must be between 0.1 and 120 seconds")
	}
	if c.EngineBinary == "" {
		c.EngineBinary = "lean-ctx"
	}
	return nil
}

func (c *SubprocessEngineClient) ContextView(plan *ContextPlan) (*ContextView, error) {
	return c.ContextViewContext(context.Background(), plan)
}

func (c *SubprocessEngineClient) ContextViewContext(parent context.Context, plan *ContextPlan) (*ContextView, error) {
	if parent == nil {
		parent = context.Background()
	}
	if err := c.configured(); err != nil {
		return nil, err
	}
	if plan == nil {
		return nil, NewValidationError("context_view requires ContextPlan")
	}
	path, err := plan.Source.RelativePath()
	if err != nil {
		return nil, err
	}
	request := map[string]any{"schema_version": int64(SchemaVersion), "transport_version": int64(TransportVersion), "engine_interface_version": EngineInterfaceVersion, "path": path, "mode": plan.Mode}
	response, err := c.invoke(parent, "context-view", plan.Source.ProjectRoot, request)
	if err != nil {
		return nil, err
	}
	if !response.HasRecords {
		return nil, protocolError("context-view response omitted invocation/observation")
	}
	refs, _ := response.Invocation["source_refs"].([]string)
	admitted := false
	for _, ref := range refs {
		if ref == response.Recovery.SourceRef {
			admitted = true
			break
		}
	}
	if !admitted {
		return nil, protocolError("recovery source_ref is not admitted by invocation")
	}
	if plan.Source.SourceRef != "" && plan.Source.SourceRef != response.Recovery.SourceRef {
		return nil, protocolError("Engine source_ref differs from requested binding")
	}
	if plan.Source.SourceDigest != "" && plan.Source.SourceDigest != response.Recovery.SourceDigest {
		return nil, protocolError("Engine source_digest differs from requested binding")
	}
	view, err := buildView(plan.Source, response)
	if err != nil {
		return nil, err
	}
	if view.Status == EngineStatusRejected {
		failure := view.Failure
		if failure != nil && failure.Code == FailureCodePolicyRejected {
			return view, NewPolicyAdmissionError("Engine rejected request: policy_rejected", failure, view)
		}
		if failure != nil && failure.Code == FailureCodeSourceUnavailable {
			return view, NewSourceUnavailableError("Engine rejected request: source_unavailable", failure, view)
		}
		return view, NewEngineRejected("Engine rejected request: rejected", failure, view)
	}
	if view.Status == EngineStatusFailed {
		failure := view.Failure
		if failure != nil && failure.Code == FailureCodeUnsupportedOperation {
			return view, NewUnsupportedEngineError("Engine execution failed: unsupported_operation")
		}
		if failure != nil && failure.Code == FailureCodeSourceIntegrityMismatch {
			return view, NewArtifactIntegrityError("Engine execution failed: source_integrity_mismatch", failure, view)
		}
		if failure != nil && failure.Code == FailureCodeSourceUnavailable {
			return view, NewSourceUnavailableError("Engine execution failed: source_unavailable", failure, view)
		}
		return view, NewEngineExecutionError("Engine execution failed: failed", failure, view)
	}
	return view, nil
}

func (c *SubprocessEngineClient) Recover(projectRoot, path, recoveryRef, sourceRef, sourceDigest string) (*RecoveredSource, error) {
	return c.RecoverContext(context.Background(), projectRoot, path, recoveryRef, sourceRef, sourceDigest)
}

func (c *SubprocessEngineClient) RecoverContext(parent context.Context, projectRoot, path, recoveryRef, sourceRef, sourceDigest string) (*RecoveredSource, error) {
	if parent == nil {
		parent = context.Background()
	}
	if err := c.configured(); err != nil {
		return nil, err
	}
	root, err := validateRoot(projectRoot)
	if err != nil {
		return nil, err
	}
	safePath, err := safeRelativePath(path)
	if err != nil {
		return nil, err
	}
	checkedRecovery, err := protocolRef(recoveryRef, "recovery_ref")
	if err != nil {
		return nil, err
	}
	checkedSource, err := protocolRef(sourceRef, "source_ref")
	if err != nil {
		return nil, err
	}
	checkedDigest, err := requiredDigest(sourceDigest, "source_digest")
	if err != nil {
		return nil, err
	}
	request := map[string]any{"schema_version": int64(SchemaVersion), "transport_version": int64(TransportVersion), "engine_interface_version": EngineInterfaceVersion, "path": safePath, "recovery_ref": checkedRecovery, "source_ref": checkedSource, "source_digest": checkedDigest}
	response, err := c.invoke(parent, "recover", root, request)
	if err != nil {
		return nil, err
	}
	if response.HasRecords {
		return nil, protocolError("recover response must have null invocation/observation")
	}
	if response.Recovery.RecoveryRef != checkedRecovery || response.Recovery.SourceRef != checkedSource || response.Recovery.SourceDigest != checkedDigest {
		return nil, NewArtifactIntegrityError("recover response binding mismatch", nil, nil)
	}
	if response.View.OutputDigest == nil || *response.View.OutputDigest != checkedDigest {
		return nil, NewArtifactIntegrityError("recover output digest does not match source digest", nil, nil)
	}
	expectedRef := "output:" + strings.TrimPrefix(checkedDigest, "sha256:")
	if response.View.OutputRef != nil && *response.View.OutputRef != expectedRef {
		return nil, NewArtifactIntegrityError("recover output reference does not match source digest", nil, nil)
	}
	recovered, err := NewRecoveredSource(response.View.Text, checkedSource, checkedDigest, checkedRecovery)
	if err != nil {
		return nil, protocolError(err.Error())
	}
	return recovered, nil
}

func validateRoot(projectRoot string) (string, error) {
	if projectRoot == "" || strings.IndexByte(projectRoot, 0) >= 0 || len([]byte(projectRoot)) > maxPathBytes {
		return "", NewSourceUnavailableError("project_root is unavailable", nil, nil)
	}
	root, err := filepath.Abs(filepath.Clean(projectRoot))
	if err != nil {
		return "", NewSourceUnavailableError("project_root is unavailable", nil, nil)
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", NewSourceUnavailableError("project_root is unavailable", nil, nil)
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return "", NewSourceUnavailableError("project_root is unavailable", nil, nil)
	}
	return root, nil
}

func (c *SubprocessEngineClient) resolveBinary() (string, error) {
	binary := c.EngineBinary
	if binary == "" {
		binary = "lean-ctx"
	}
	if !filepath.IsAbs(binary) && !strings.ContainsAny(binary, `/\\`) {
		found := ""
		for _, directory := range filepath.SplitList(os.Getenv("PATH")) {
			if directory == "" {
				continue
			}
			candidate := filepath.Join(directory, binary)
			if isExecutableRegular(candidate) {
				found = candidate
				break
			}
		}
		if found == "" {
			return "", NewEngineUnavailable("configured Engine binary is unavailable")
		}
		binary = found
	} else {
		var err error
		binary, err = filepath.Abs(binary)
		if err != nil {
			return "", NewEngineUnavailable("configured Engine binary is unavailable")
		}
	}
	if !isExecutableRegular(binary) {
		return "", NewEngineUnavailable("configured Engine binary is unavailable")
	}
	resolved, err := filepath.EvalSymlinks(binary)
	if err != nil || !isExecutableRegular(resolved) {
		return "", NewEngineUnavailable("configured Engine binary is unavailable")
	}
	return resolved, nil
}

func isExecutableRegular(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular() && info.Mode()&0111 != 0
}

func (c *SubprocessEngineClient) invoke(parent context.Context, operation, root string, request map[string]any) (parsedResponse, error) {
	validatedRoot, err := validateRoot(root)
	if err != nil {
		return parsedResponse{}, err
	}
	payload, err := canonicalJSON(request)
	if err != nil {
		return parsedResponse{}, NewEngineProtocolError("Engine request is not canonical JSON")
	}
	if len(payload) > maxRequestBytes {
		return parsedResponse{}, NewEngineProtocolError("Engine request exceeds the bound")
	}
	temporary, err := os.CreateTemp(validatedRoot, ".leanctx-sdk-*.json")
	if err != nil {
		return parsedResponse{}, NewEngineUnavailable("Engine request file could not be created")
	}
	requestPath := temporary.Name()
	defer os.Remove(requestPath)
	if err := temporary.Chmod(0600); err != nil {
		temporary.Close()
		return parsedResponse{}, NewEngineUnavailable("Engine request file could not be secured")
	}
	if _, err := temporary.Write(payload); err != nil {
		temporary.Close()
		return parsedResponse{}, NewEngineUnavailable("Engine request file could not be written")
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return parsedResponse{}, NewEngineUnavailable("Engine request file could not be synced")
	}
	if err := temporary.Close(); err != nil {
		return parsedResponse{}, NewEngineUnavailable("Engine request file could not be closed")
	}
	raw, err := c.run(parent, operation, validatedRoot, requestPath)
	if err != nil {
		return parsedResponse{}, err
	}
	return parseResponse(raw)
}

type streamResult struct {
	name string
	data []byte
	err  error
}

func collectStream(reader io.Reader, name string, maximum int, results chan<- streamResult, overflow chan<- string) {
	var data bytes.Buffer
	buffer := make([]byte, 32*1024)
	for {
		count, err := reader.Read(buffer)
		if count > 0 {
			if data.Len()+count > maximum {
				select {
				case overflow <- name:
				default:
				}
				return
			}
			_, _ = data.Write(buffer[:count])
		}
		if err != nil {
			results <- streamResult{name: name, data: data.Bytes(), err: err}
			return
		}
	}
}

func (c *SubprocessEngineClient) run(parent context.Context, operation, root, requestPath string) ([]byte, error) {
	binary, err := c.resolveBinary()
	if err != nil {
		return nil, err
	}
	args := []string{"engine", operation, "--project-root", root, "--json-file", requestPath}
	command := exec.Command(binary, args...)
	command.Dir = root
	command.Env = []string{"LC_ALL=C", "LANG=C", "TZ=UTC", "PYTHONHASHSEED=0"}
	configureProcessGroup(command)
	stdout, err := command.StdoutPipe()
	if err != nil {
		return nil, NewEngineUnavailable("Engine stdout could not be opened")
	}
	stderr, err := command.StderrPipe()
	if err != nil {
		return nil, NewEngineUnavailable("Engine stderr could not be opened")
	}
	if err := command.Start(); err != nil {
		return nil, NewEngineUnavailable("Engine process could not be started")
	}
	results := make(chan streamResult, 2)
	overflow := make(chan string, 1)
	go collectStream(stdout, "stdout", maxResponseBytes, results, overflow)
	go collectStream(stderr, "stderr", maxStderrBytes, results, overflow)
	wait := make(chan error, 1)
	go func() { wait <- command.Wait() }()
	deadline := time.NewTimer(c.Timeout)
	defer deadline.Stop()
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	var waitErr error
	select {
	case <-ctx.Done():
		terminateProcess(command)
		waitErr = <-wait
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, NewEngineTimeout("Engine process exceeded its deadline")
		}
		return nil, ctx.Err()
	case <-deadline.C:
		terminateProcess(command)
		waitErr = <-wait
		return nil, NewEngineTimeout("Engine process exceeded its deadline")
	case <-overflow:
		terminateProcess(command)
		waitErr = <-wait
		_ = waitErr
		return nil, NewEngineProtocolError("Engine process output exceeds its bound")
	case waitErr = <-wait:
	}
	first := <-results
	second := <-results
	var stdoutData, stderrData []byte
	for _, result := range []streamResult{first, second} {
		if result.name == "stdout" {
			stdoutData = result.data
		} else {
			stderrData = result.data
		}
	}
	if waitErr != nil {
		code := stderrEngineCode(stderrData)
		switch code {
		case "unsafe_root", "source_outside_root", "source_symlink", "policy_rejected":
			return nil, NewPolicyAdmissionError("Engine rejected request: "+code, nil, nil)
		case "source_unavailable":
			return nil, NewSourceUnavailableError("Engine source is unavailable", nil, nil)
		case "unsupported_mode":
			return nil, NewUnsupportedEngineError("Engine operation is unsupported")
		default:
			if code == "" {
				code = "nonzero_exit"
			}
			return nil, NewEngineExecutionError("Engine process failed: "+code, nil, nil)
		}
	}
	if len(stdoutData) == 0 {
		return nil, NewEngineProtocolError("Engine returned empty stdout")
	}
	if !utf8.Valid(stdoutData) {
		return nil, NewEngineProtocolError("Engine returned invalid UTF-8")
	}
	return stdoutData, nil
}

func stderrEngineCode(stderr []byte) string {
	for _, line := range strings.Split(string(stderr), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "engine:") {
			code := strings.TrimSpace(strings.TrimPrefix(line, "engine:"))
			if code != "" {
				return code
			}
		}
	}
	return ""
}

func buildView(source ContextSource, response parsedResponse) (*ContextView, error) {
	if !response.HasRecords {
		return nil, protocolError("Engine records are malformed")
	}
	if refs, ok := response.Invocation["source_refs"].([]string); !ok || !contains(refs, response.Recovery.SourceRef) {
		return nil, protocolError("recovery source_ref is not in invocation lineage")
	}
	options := ContextViewOptions{Source: source, Text: &response.View.Text, OutputRef: response.View.OutputRef, OutputDigest: response.View.OutputDigest, SourceRef: response.Recovery.SourceRef, SourceDigest: response.Recovery.SourceDigest, RecoveryRef: response.Recovery.RecoveryRef, Status: EngineStatus(response.Observation["status"].(string)), Invocation: response.Invocation, Observation: response.Observation}
	if value, ok := response.Observation["measurements"].([]ContextMeasurement); ok {
		options.Measurements = value
	}
	if value, ok := response.Observation["failure"].(*ContextFailure); ok {
		options.Failure = value
	}
	if value, ok := response.Observation["receipt_link"].(*ContextReceiptLink); ok {
		options.ReceiptLink = value
	}
	view, err := NewContextView(options)
	if err != nil {
		return nil, protocolError(err.Error())
	}
	if view.Status == EngineStatusSucceeded && !view.Verify() {
		return nil, NewArtifactIntegrityError("succeeded Engine evidence is not sealed", nil, view)
	}
	return view, nil
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func pointerOrNil[T any](value *T) any {
	if value == nil {
		return nil
	}
	return *value
}
