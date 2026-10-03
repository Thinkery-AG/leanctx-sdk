// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

// Preview: the information gateway's egress admission and decision receipts.
//
// AdmitEgress runs one model request through the local Engine's egress
// admission (`lean-ctx engine egress-admit`) before the caller sends it:
// secrets are masked, restricted content is withheld, and the request is
// classified. Parsing mirrors the Engine's validation; unknown fields and
// values are rejected. Contract leanctx-gateway-preview 0.1 — may change in
// minor releases.

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"slices"
	"strings"
	"unicode/utf8"
)

const (
	GatewayPreviewContract = "leanctx-gateway-preview"
	GatewayPreviewVersion  = "0.1.0"
	EgressSchemaVersion    = 1
	MaxEgressRequestBytes  = 8 * 1024 * 1024
	gatewayMaxRefBytes     = 512
	gatewayMaxDecisions    = 4096
	gatewayMaxReasonCodes  = 32
	gatewayMaxSignals      = 32
	gatewayU32             = math.MaxUint32
	gatewayMaxSafe         = 1<<53 - 1
)

var (
	gatewayDigest = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	gatewayReason = regexp.MustCompile(`^[a-z][a-z0-9_.]{2,63}$`)

	gatewayClassifications = []string{"public", "internal", "confidential", "restricted"}
	gatewayDispositions    = []string{"forward", "rewritten", "refused"}
	gatewayModes           = []string{"developer", "governed", "sovereign"}
	gatewayPrincipalKinds  = []string{"person", "team", "organization", "project", "agent", "session", "workload", "unknown"}
	gatewayLocalities      = []string{"local", "remote", "unknown"}
	gatewayOutcomes        = []string{"delivered", "withheld", "failed"}
	gatewayContextDisps    = []string{"allow", "allow_minimized", "allow_redacted", "allow_summary_only", "allow_local_model_only", "allow_with_approval", "quarantine", "deny"}
	gatewayTransformations = []string{"redaction", "classification", "selection", "deduplication", "structural_extraction", "compression", "summarization", "recovery", "reranking"}
	gatewayCategories      = []string{"secret", "pii", "prompt_injection", "classification", "policy", "custom"}
	gatewaySeverities      = []string{"info", "low", "medium", "high", "critical"}
	gatewayCoverageKinds   = []string{"complete", "partial", "unsupported", "failed", "not_required"}
	gatewayDetectorStatus  = []string{"completed", "failed", "timed_out", "skipped"}
)

// ContextPrincipal is who requested the context; "unknown" never authorizes.
type ContextPrincipal struct {
	Kind string
	ID   *string
}

// ContextDestination is where the context goes.
type ContextDestination struct {
	Provider            string
	Locality            string
	Model               *string
	OrganizationManaged bool
	AccountRef          *string
	Region              *string
}

// DetectorCoverage is what a detector actually inspected.
type DetectorCoverage struct {
	Kind            string
	BytesTotal      int64
	BytesInspected  int64
	ChunksTotal     int64
	ChunksInspected int64
	Reason          *string
}

// SecuritySignal is one detector's result: counts only, never the value.
type SecuritySignal struct {
	DetectorID      string
	DetectorVersion string
	Category        string
	Severity        string
	EvidenceCount   int64
	Coverage        DetectorCoverage
	Status          string
	LatencyUs       int64
	Calibrated      bool
	ConfidenceMilli *int64
}

// ContextDecision is the gateway's decision about one object (by digest).
type ContextDecision struct {
	Object                  string
	Disposition             string
	ReasonCodes             []string
	Signals                 []SecuritySignal
	RequiredTransformations []string
}

// DeliversContent reports whether (possibly transformed) content goes out.
func (d ContextDecision) DeliversContent() bool {
	return slices.Index(gatewayContextDisps, d.Disposition) <= slices.Index(gatewayContextDisps, "allow_local_model_only")
}

// ContextDecisionReceipt is one governed delivery.
type ContextDecisionReceipt struct {
	ReceiptID    string
	Mode         string
	Principal    ContextPrincipal
	Destination  ContextDestination
	Sources      map[string]int64
	Security     map[string]int64
	Tokens       map[string]int64
	Outcome      string
	DurationUs   int64
	Decisions    []ContextDecision
	Policy       map[string]string
	Task         *string
	FinalContext *string
	Quality      map[string]any
}

// Signals flattens the signals of every decision.
func (r ContextDecisionReceipt) Signals() []SecuritySignal {
	var signals []SecuritySignal
	for _, decision := range r.Decisions {
		signals = append(signals, decision.Signals...)
	}
	return signals
}

// EgressAdmission is what may leave for the model, how sensitive it is, and why.
type EgressAdmission struct {
	Disposition    string
	Body           map[string]any
	Refusal        *string
	Classification *string
	Receipt        *ContextDecisionReceipt
}

// MaySend reports whether Body may be sent; a refused request must not be.
func (a EgressAdmission) MaySend() bool { return a.Disposition != "refused" }

// EgressRequest is one model request about to leave for a provider.
type EgressRequest struct {
	Provider     string
	UpstreamBase string
	Body         map[string]any
}

func gatewayBad(format string, args ...any) error {
	return NewEngineProtocolError("egress admission: " + fmt.Sprintf(format, args...))
}

func gwObject(value any, label string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, gatewayBad("%s must be an object", label)
	}
	return object, nil
}

func gwKeys(value map[string]any, required, optional []string, label string) error {
	for _, key := range required {
		if _, ok := value[key]; !ok {
			return gatewayBad("%s lacks %s", label, key)
		}
	}
	for key := range value {
		if !slices.Contains(required, key) && !slices.Contains(optional, key) {
			return gatewayBad("%s has unknown field %s", label, key)
		}
	}
	return nil
}

func gwInt(value any, label string, maximum int64) (int64, error) {
	number, ok := value.(json.Number)
	if !ok || strings.ContainsAny(number.String(), ".eE") {
		return 0, gatewayBad("%s must be an integer", label)
	}
	parsed, err := number.Int64()
	if err != nil || parsed < 0 || parsed > maximum {
		return 0, gatewayBad("%s must be an integer in 0..%d", label, maximum)
	}
	return parsed, nil
}

func gwBool(value any, fallback bool, present bool, label string) (bool, error) {
	if !present {
		return fallback, nil
	}
	parsed, ok := value.(bool)
	if !ok {
		return false, gatewayBad("%s must be a boolean", label)
	}
	return parsed, nil
}

func gwRef(value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || strings.TrimSpace(text) == "" || len(text) > gatewayMaxRefBytes || !utf8.ValidString(text) {
		return "", gatewayBad("%s must be a bounded reference", label)
	}
	for _, character := range text {
		if character < 0x20 || character == 0x7f {
			return "", gatewayBad("%s must be a bounded reference", label)
		}
	}
	return text, nil
}

func gwOptionalRef(value map[string]any, key, label string) (*string, error) {
	raw, ok := value[key]
	if !ok {
		return nil, nil
	}
	parsed, err := gwRef(raw, label)
	if err != nil {
		return nil, err
	}
	return &parsed, nil
}

func gwEnum(value any, allowed []string, label string) (string, error) {
	text, ok := value.(string)
	if !ok || !slices.Contains(allowed, text) {
		return "", gatewayBad("%s has unknown value %v", label, value)
	}
	return text, nil
}

func gwDigest(value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || !gatewayDigest.MatchString(text) {
		return "", gatewayBad("%s must be a sha256 digest", label)
	}
	return text, nil
}

func gwReasons(value any, label string) ([]string, error) {
	list, ok := value.([]any)
	if !ok || len(list) > gatewayMaxReasonCodes {
		return nil, gatewayBad("%s must be a bounded list", label)
	}
	codes := make([]string, 0, len(list))
	for _, item := range list {
		code, ok := item.(string)
		if !ok || !gatewayReason.MatchString(code) {
			return nil, gatewayBad("%s holds an invalid reason code", label)
		}
		codes = append(codes, code)
	}
	return codes, nil
}

func gwList(value map[string]any, key string) (any, bool) {
	raw, ok := value[key]
	if !ok {
		return []any{}, false
	}
	return raw, true
}

func parsePrincipal(raw any) (ContextPrincipal, error) {
	value, err := gwObject(raw, "principal")
	if err != nil {
		return ContextPrincipal{}, err
	}
	if err := gwKeys(value, []string{"kind"}, []string{"id"}, "principal"); err != nil {
		return ContextPrincipal{}, err
	}
	kind, err := gwEnum(value["kind"], gatewayPrincipalKinds, "principal.kind")
	if err != nil {
		return ContextPrincipal{}, err
	}
	_, hasID := value["id"]
	if kind == "unknown" {
		if hasID {
			return ContextPrincipal{}, gatewayBad("an unknown principal must not carry an identity")
		}
		return ContextPrincipal{Kind: kind}, nil
	}
	if !hasID {
		return ContextPrincipal{}, gatewayBad("a known principal requires an id")
	}
	id, err := gwRef(value["id"], "principal.id")
	if err != nil {
		return ContextPrincipal{}, err
	}
	return ContextPrincipal{Kind: kind, ID: &id}, nil
}

func parseDestination(raw any) (ContextDestination, error) {
	value, err := gwObject(raw, "destination")
	if err != nil {
		return ContextDestination{}, err
	}
	if err := gwKeys(value, []string{"provider", "locality"}, []string{"model", "organization_managed", "account_ref", "region"}, "destination"); err != nil {
		return ContextDestination{}, err
	}
	var destination ContextDestination
	if destination.Provider, err = gwRef(value["provider"], "destination.provider"); err != nil {
		return destination, err
	}
	if destination.Locality, err = gwEnum(value["locality"], gatewayLocalities, "destination.locality"); err != nil {
		return destination, err
	}
	managed, present := value["organization_managed"]
	if destination.OrganizationManaged, err = gwBool(managed, false, present, "destination.organization_managed"); err != nil {
		return destination, err
	}
	if destination.Model, err = gwOptionalRef(value, "model", "destination.model"); err != nil {
		return destination, err
	}
	if destination.AccountRef, err = gwOptionalRef(value, "account_ref", "destination.account_ref"); err != nil {
		return destination, err
	}
	if destination.Region, err = gwOptionalRef(value, "region", "destination.region"); err != nil {
		return destination, err
	}
	return destination, nil
}

func parseCoverage(raw any) (DetectorCoverage, error) {
	value, err := gwObject(raw, "coverage")
	if err != nil {
		return DetectorCoverage{}, err
	}
	if err := gwKeys(value, []string{"kind", "bytes_total", "bytes_inspected", "chunks_total", "chunks_inspected"}, []string{"reason"}, "coverage"); err != nil {
		return DetectorCoverage{}, err
	}
	var coverage DetectorCoverage
	if coverage.Kind, err = gwEnum(value["kind"], gatewayCoverageKinds, "coverage.kind"); err != nil {
		return coverage, err
	}
	if coverage.BytesTotal, err = gwInt(value["bytes_total"], "coverage.bytes_total", gatewayMaxSafe); err != nil {
		return coverage, err
	}
	if coverage.BytesInspected, err = gwInt(value["bytes_inspected"], "coverage.bytes_inspected", gatewayMaxSafe); err != nil {
		return coverage, err
	}
	if coverage.ChunksTotal, err = gwInt(value["chunks_total"], "coverage.chunks_total", gatewayU32); err != nil {
		return coverage, err
	}
	if coverage.ChunksInspected, err = gwInt(value["chunks_inspected"], "coverage.chunks_inspected", gatewayU32); err != nil {
		return coverage, err
	}
	if raw, ok := value["reason"]; ok {
		codes, err := gwReasons([]any{raw}, "coverage.reason")
		if err != nil {
			return coverage, err
		}
		coverage.Reason = &codes[0]
	}
	if coverage.BytesInspected > coverage.BytesTotal || coverage.ChunksInspected > coverage.ChunksTotal {
		return coverage, gatewayBad("coverage must not inspect more than the object holds")
	}
	allBytes := coverage.BytesInspected == coverage.BytesTotal
	if coverage.Kind == "complete" && !allBytes {
		return coverage, gatewayBad("complete coverage must inspect every byte")
	}
	if coverage.Kind == "partial" && allBytes {
		return coverage, gatewayBad("partial coverage must leave bytes uninspected")
	}
	return coverage, nil
}

func parseSignal(raw any) (SecuritySignal, error) {
	value, err := gwObject(raw, "signal")
	if err != nil {
		return SecuritySignal{}, err
	}
	if err := gwKeys(value, []string{"detector", "category", "severity", "evidence_count", "coverage", "status", "latency_us"}, []string{"calibrated", "confidence_milli"}, "signal"); err != nil {
		return SecuritySignal{}, err
	}
	detector, err := gwObject(value["detector"], "signal.detector")
	if err != nil {
		return SecuritySignal{}, err
	}
	if err := gwKeys(detector, []string{"id", "version"}, nil, "signal.detector"); err != nil {
		return SecuritySignal{}, err
	}
	var signal SecuritySignal
	if signal.DetectorID, err = gwRef(detector["id"], "signal.detector.id"); err != nil {
		return signal, err
	}
	if signal.DetectorVersion, err = gwRef(detector["version"], "signal.detector.version"); err != nil {
		return signal, err
	}
	if signal.Category, err = gwEnum(value["category"], gatewayCategories, "signal.category"); err != nil {
		return signal, err
	}
	if signal.Severity, err = gwEnum(value["severity"], gatewaySeverities, "signal.severity"); err != nil {
		return signal, err
	}
	if signal.EvidenceCount, err = gwInt(value["evidence_count"], "signal.evidence_count", gatewayU32); err != nil {
		return signal, err
	}
	if signal.Coverage, err = parseCoverage(value["coverage"]); err != nil {
		return signal, err
	}
	if signal.Status, err = gwEnum(value["status"], gatewayDetectorStatus, "signal.status"); err != nil {
		return signal, err
	}
	if signal.LatencyUs, err = gwInt(value["latency_us"], "signal.latency_us", gatewayMaxSafe); err != nil {
		return signal, err
	}
	calibrated, present := value["calibrated"]
	if signal.Calibrated, err = gwBool(calibrated, false, present, "signal.calibrated"); err != nil {
		return signal, err
	}
	if raw, ok := value["confidence_milli"]; ok {
		confidence, err := gwInt(raw, "signal.confidence_milli", 1000)
		if err != nil {
			return signal, err
		}
		signal.ConfidenceMilli = &confidence
	}
	if (signal.Status == "failed" || signal.Status == "timed_out") && signal.Coverage.Kind == "complete" {
		return signal, gatewayBad("a failed or timed-out detector cannot claim complete coverage")
	}
	return signal, nil
}

func parseDecision(raw any) (ContextDecision, error) {
	value, err := gwObject(raw, "decision")
	if err != nil {
		return ContextDecision{}, err
	}
	if err := gwKeys(value, []string{"object", "disposition"}, []string{"reason_codes", "signals", "required_transformations"}, "decision"); err != nil {
		return ContextDecision{}, err
	}
	var decision ContextDecision
	if decision.Object, err = gwDigest(value["object"], "decision.object"); err != nil {
		return decision, err
	}
	if decision.Disposition, err = gwEnum(value["disposition"], gatewayContextDisps, "decision.disposition"); err != nil {
		return decision, err
	}
	reasons, _ := gwList(value, "reason_codes")
	if decision.ReasonCodes, err = gwReasons(reasons, "decision.reason_codes"); err != nil {
		return decision, err
	}
	signals, _ := gwList(value, "signals")
	list, ok := signals.([]any)
	if !ok || len(list) > gatewayMaxSignals {
		return decision, gatewayBad("decision.signals must be a bounded list")
	}
	for _, item := range list {
		signal, err := parseSignal(item)
		if err != nil {
			return decision, err
		}
		decision.Signals = append(decision.Signals, signal)
	}
	transformations, _ := gwList(value, "required_transformations")
	kinds, ok := transformations.([]any)
	if !ok {
		return decision, gatewayBad("decision.required_transformations must be a list")
	}
	for _, kind := range kinds {
		parsed, err := gwEnum(kind, gatewayTransformations, "decision.required_transformations")
		if err != nil {
			return decision, err
		}
		decision.RequiredTransformations = append(decision.RequiredTransformations, parsed)
	}
	if decision.Disposition != "allow" && len(decision.ReasonCodes) == 0 {
		return decision, gatewayBad("every non-allow decision requires at least one reason code")
	}
	return decision, nil
}

func gwCounts(raw any, names []string, label string, maximum int64) (map[string]int64, error) {
	value, err := gwObject(raw, label)
	if err != nil {
		return nil, err
	}
	if err := gwKeys(value, names, nil, label); err != nil {
		return nil, err
	}
	counts := make(map[string]int64, len(names))
	for _, name := range names {
		if counts[name], err = gwInt(value[name], label+"."+name, maximum); err != nil {
			return nil, err
		}
	}
	return counts, nil
}

// ParseDecisionReceipt parses and validates a ContextDecisionReceiptV1 document.
func ParseDecisionReceipt(raw any) (*ContextDecisionReceipt, error) {
	value, err := gwObject(raw, "receipt")
	if err != nil {
		return nil, err
	}
	if err := gwKeys(value, []string{"schema_version", "receipt_id", "mode", "principal", "destination", "sources", "security", "tokens", "outcome", "duration_us"}, []string{"task", "policy", "decisions", "final_context", "quality"}, "receipt"); err != nil {
		return nil, err
	}
	if version, err := gwInt(value["schema_version"], "receipt.schema_version", gatewayMaxSafe); err != nil || version != 1 {
		return nil, gatewayBad("unsupported receipt schema_version")
	}
	receipt := &ContextDecisionReceipt{}
	decisions, _ := gwList(value, "decisions")
	list, ok := decisions.([]any)
	if !ok || len(list) > gatewayMaxDecisions {
		return nil, gatewayBad("receipt.decisions must be a bounded list")
	}
	denied, quarantined := int64(0), int64(0)
	for _, item := range list {
		decision, err := parseDecision(item)
		if err != nil {
			return nil, err
		}
		switch decision.Disposition {
		case "deny":
			denied++
		case "quarantine":
			quarantined++
		}
		receipt.Decisions = append(receipt.Decisions, decision)
	}
	if receipt.Sources, err = gwCounts(value["sources"], []string{"inspected", "permitted", "selected", "blocked"}, "receipt.sources", gatewayU32); err != nil {
		return nil, err
	}
	sources := receipt.Sources
	if sources["selected"] > sources["permitted"] || sources["permitted"]+sources["blocked"] > sources["inspected"] {
		return nil, gatewayBad("source counts must satisfy selected <= permitted and permitted + blocked <= inspected")
	}
	if receipt.Security, err = gwCounts(value["security"], []string{"redactions", "blocked_objects", "quarantined_objects", "injection_signals", "incomplete_coverage"}, "receipt.security", gatewayU32); err != nil {
		return nil, err
	}
	if receipt.Security["blocked_objects"] != denied || receipt.Security["quarantined_objects"] != quarantined {
		return nil, gatewayBad("security counts must equal the recorded deny/quarantine decisions")
	}
	if receipt.Tokens, err = gwCounts(value["tokens"], []string{"original", "delivered"}, "receipt.tokens", gatewayMaxSafe); err != nil {
		return nil, err
	}
	if receipt.Outcome, err = gwEnum(value["outcome"], gatewayOutcomes, "receipt.outcome"); err != nil {
		return nil, err
	}
	if raw, ok := value["final_context"]; ok {
		digest, err := gwDigest(raw, "receipt.final_context")
		if err != nil {
			return nil, err
		}
		receipt.FinalContext = &digest
	}
	if (receipt.Outcome == "delivered") != (receipt.FinalContext != nil) {
		return nil, gatewayBad("exactly a delivered receipt names the delivered context digest")
	}
	if raw, ok := value["policy"]; ok {
		policy, err := gwObject(raw, "receipt.policy")
		if err != nil {
			return nil, err
		}
		if err := gwKeys(policy, []string{"id", "digest"}, []string{"version"}, "receipt.policy"); err != nil {
			return nil, err
		}
		receipt.Policy = map[string]string{}
		if receipt.Policy["id"], err = gwRef(policy["id"], "receipt.policy.id"); err != nil {
			return nil, err
		}
		if receipt.Policy["digest"], err = gwDigest(policy["digest"], "receipt.policy.digest"); err != nil {
			return nil, err
		}
		if version, ok := policy["version"]; ok {
			if receipt.Policy["version"], err = gwRef(version, "receipt.policy.version"); err != nil {
				return nil, err
			}
		}
	}
	if receipt.ReceiptID, err = gwRef(value["receipt_id"], "receipt.receipt_id"); err != nil {
		return nil, err
	}
	if receipt.Mode, err = gwEnum(value["mode"], gatewayModes, "receipt.mode"); err != nil {
		return nil, err
	}
	if receipt.Principal, err = parsePrincipal(value["principal"]); err != nil {
		return nil, err
	}
	if receipt.Destination, err = parseDestination(value["destination"]); err != nil {
		return nil, err
	}
	if receipt.DurationUs, err = gwInt(value["duration_us"], "receipt.duration_us", gatewayMaxSafe); err != nil {
		return nil, err
	}
	if receipt.Task, err = gwOptionalRef(value, "task", "receipt.task"); err != nil {
		return nil, err
	}
	if raw, ok := value["quality"]; ok {
		if receipt.Quality, err = gwObject(raw, "receipt.quality"); err != nil {
			return nil, err
		}
	}
	return receipt, nil
}

// ParseEgressAdmission parses and validates an EngineEgressAdmissionResponseV1 document.
func ParseEgressAdmission(raw any) (*EgressAdmission, error) {
	value, err := gwObject(raw, "response")
	if err != nil {
		return nil, err
	}
	if err := gwKeys(value, []string{"schema_version", "disposition"}, []string{"body", "refusal", "classification", "receipt"}, "response"); err != nil {
		return nil, err
	}
	if version, err := gwInt(value["schema_version"], "schema_version", gatewayMaxSafe); err != nil || version != EgressSchemaVersion {
		return nil, gatewayBad("unsupported egress schema_version")
	}
	admission := &EgressAdmission{}
	if admission.Disposition, err = gwEnum(value["disposition"], gatewayDispositions, "disposition"); err != nil {
		return nil, err
	}
	rawBody, hasBody := value["body"]
	rawRefusal, hasRefusal := value["refusal"]
	if admission.Disposition == "refused" {
		refusal, ok := rawRefusal.(string)
		if hasBody || !hasRefusal || !ok || strings.TrimSpace(refusal) == "" {
			return nil, gatewayBad("a refused request carries a refusal and no body")
		}
		admission.Refusal = &refusal
	} else {
		body, ok := rawBody.(map[string]any)
		if hasRefusal || !hasBody || !ok {
			return nil, gatewayBad("an admitted request carries a body object and no refusal")
		}
		admission.Body = body
	}
	if raw, ok := value["classification"]; ok {
		classification, err := gwEnum(raw, gatewayClassifications, "classification")
		if err != nil {
			return nil, err
		}
		admission.Classification = &classification
	}
	if raw, ok := value["receipt"]; ok {
		if admission.Receipt, err = ParseDecisionReceipt(raw); err != nil {
			return nil, err
		}
	}
	return admission, nil
}

// AdmitEgress admits one model request through the local Engine before it is
// sent. Send admission.Body, never the original body, and only when MaySend.
func (c *SubprocessEngineClient) AdmitEgress(parent context.Context, projectRoot string, request EgressRequest) (*EgressAdmission, error) {
	if strings.TrimSpace(request.Provider) == "" {
		return nil, NewValidationError("provider must be a non-empty string")
	}
	if !strings.HasPrefix(request.UpstreamBase, "https://") && !strings.HasPrefix(request.UpstreamBase, "http://") {
		return nil, NewValidationError("upstream base must be an http(s) URL")
	}
	if request.Body == nil {
		return nil, NewValidationError("body must be a JSON object")
	}
	if err := c.configured(); err != nil {
		return nil, err
	}
	root, err := validateRoot(projectRoot)
	if err != nil {
		return nil, err
	}
	// Model requests carry fractional numbers (temperature, top_p): plain JSON.
	payload, err := json.Marshal(map[string]any{
		"schema_version": EgressSchemaVersion,
		"provider":       request.Provider,
		"upstream_base":  request.UpstreamBase,
		"body":           request.Body,
	})
	if err != nil {
		return nil, NewValidationError("body is not JSON data")
	}
	if len(payload) > MaxEgressRequestBytes {
		return nil, NewValidationError("egress request exceeds its bound")
	}
	raw, err := c.runPayload(parent, "egress-admit", root, payload)
	if err != nil {
		return nil, err
	}
	document, err := strictJSONLoads(raw, "egress admission")
	if err != nil {
		return nil, gatewayBad("response is not strict JSON")
	}
	return ParseEgressAdmission(document)
}
