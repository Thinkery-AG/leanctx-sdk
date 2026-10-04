// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

// Preview: read-only access to the local Engine's Context Store.
//
// ReadPolicyEvidence returns the scope's content-free read-strategy evidence
// (`lean-ctx engine context-policy-evidence`); ReadTaskLineage one task's
// decision lineage (`lean-ctx engine context-lineage`) with every missing
// link named as a gap. Both reads are confined to one tenant/project scope.
// Parsing mirrors the Engine's validation: unknown fields or values and
// inconsistent counts are rejected, and "unmeasured" never reads as a passing
// measurement. Contract leanctx-context-store-preview 0.1 — may change in
// minor releases.

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"unicode/utf8"
)

const (
	ContextStorePreviewContract = "leanctx-context-store-preview"
	ContextStorePreviewVersion  = "0.1.0"
	maxStoreRequestBytes        = 16 * 1024
	maxStoreResponseBytes       = 8 * 1024 * 1024
	storeMaxRecords             = 4096
	storeMaxLineageItems        = 4096
	storeMaxRefBytes            = 512
	storeMaxTextBytes           = 4096
	storeMaxSafe                = 1<<53 - 1
)

var (
	storeDigest = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	storeField  = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)

	storeTaskClasses      = []string{"bug_fix", "refactor", "test_addition", "documentation", "investigation"}
	storeLanguages        = []string{"rust", "python", "type_script", "java_script", "go", "java", "c", "cpp", "c_sharp", "swift", "kotlin", "ruby", "php", "shell", "other", "none"}
	storeSizes            = []string{"tiny", "small", "medium", "large", "very_large"}
	storeStrategies       = []string{"full", "map", "signatures", "aggressive", "entropy", "task", "reference", "diff", "lines", "auto", "other"}
	storeEvidenceTiers    = []string{"mechanism", "deterministic_quality", "recorded_regression", "live_task_evaluation", "production_outcome"}
	storeVerdicts         = []string{"improved", "non_inferior", "regressed", "underpowered"}
	storeStepKinds        = []string{"task_started", "plan_created", "context_delivered", "model_invoked", "engine_invoked", "receipt_signed", "canonical_receipt_recorded", "outcome_recorded", "decision_recorded"}
	storeOutcomes         = []string{"accepted", "rejected", "unknown"}
	storeDeliveryOutcomes = []string{"delivered", "withheld", "failed"}
	storeDeliveryErrors   = []string{"missing", "unreadable", "tampered"}
	storeGaps             = []string{"ledger_unverified", "no_plan_recorded", "no_delivery_recorded", "delivery_unverified", "deliveries_incomplete", "no_outcome_recorded"}
)

// Workload is a plan's task class, dominant language and budget bucket.
type Workload struct {
	TaskClass string
	Language  string
	Size      string
}

// QualityEvidence: when Measured is false every count is nil, never zero.
type QualityEvidence struct {
	Measured         bool
	Retained         *int64
	Recoverable      *int64
	Lost             *int64
	HandlesEmitted   *int64
	HandlesVerified  *int64
	Failures         *int64
	CriticalFailures *int64
}

// SecurityEvidence counts deliveries that left without full inspection.
type SecurityEvidence struct {
	Measured    bool
	Regressions *int64
}

// StrategyOutcomeRecord is one read strategy on one workload, on one UTC day.
type StrategyOutcomeRecord struct {
	Workload          Workload
	Strategy          string
	ObservedDay       int64
	Samples           int64
	Accepted          int64
	Rejected          int64
	ExplicitOverrides int64
	TokenSamples      int64
	SignalSamples     int64
	BounceTasks       int64
	ExpandTasks       int64
	EditFailureTasks  int64
	TokensOriginal    int64
	TokensDelivered   int64
	Quality           QualityEvidence
	Security          SecurityEvidence
}

// StrategyEvaluation is a paired task evaluation (`lean-ctx eval frontier`).
type StrategyEvaluation struct {
	Strategy     string
	EvidenceTier string
	Verdict      string
	Pairs        int64
	Powered      bool
	DeltaMilli   int64
	CILowMilli   int64
	CIHighMilli  int64
	MarginMilli  int64
}

// ContextPolicyEvidence is one scope's ContextPolicyEvidenceV1.
type ContextPolicyEvidence struct {
	Records     []StrategyOutcomeRecord
	Evaluations []StrategyEvaluation
}

// LineageStep is one ledger observation: identifiers and counts, no content.
type LineageStep struct {
	Sequence  int64
	Kind      string
	Timestamp string
	Fields    map[string]string
}

// DeliverySummary summarizes one verified Decision Receipt.
type DeliverySummary struct {
	Outcome         string
	Destination     string
	PolicyDigest    *string
	Inspected       int64
	Delivered       int64
	Withheld        int64
	Redactions      int64
	TokensOriginal  int64
	TokensDelivered int64
	FinalContext    *string
}

// LineageDelivery is one governed delivery; unverified ones name their error.
type LineageDelivery struct {
	Digest   string
	Verified bool
	Error    *string
	Summary  *DeliverySummary
}

// TaskLineage is one task's lineage; Gaps lists every missing link.
type TaskLineage struct {
	TaskID      string
	Scope       *string
	Steps       []LineageStep
	Deliveries  []LineageDelivery
	LedgerError *string
	Outcome     string
	Gaps        []string
}

// IsComplete reports whether no link of plan → delivery → outcome is missing.
func (l TaskLineage) IsComplete() bool { return len(l.Gaps) == 0 }

// ContextStoreScope names the tenant/project scope of a read; an empty
// ProjectID lets the Engine use the project root.
type ContextStoreScope struct {
	ProjectID string
	TenantID  string
}

func storeBad(format string, args ...any) error {
	return NewEngineProtocolError("context store: " + fmt.Sprintf(format, args...))
}

func storeObject(value any, label string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, storeBad("%s must be an object", label)
	}
	return object, nil
}

func storeKeys(value map[string]any, required, optional []string, label string) error {
	for _, key := range required {
		if _, ok := value[key]; !ok {
			return storeBad("%s lacks %s", label, key)
		}
	}
	for key := range value {
		if !slices.Contains(required, key) && !slices.Contains(optional, key) {
			return storeBad("%s has unknown field %s", label, key)
		}
	}
	return nil
}

func storeInt(value any, label string, signed bool) (int64, error) {
	number, ok := value.(json.Number)
	if !ok || strings.ContainsAny(number.String(), ".eE") {
		return 0, storeBad("%s must be an integer", label)
	}
	parsed, err := number.Int64()
	minimum := int64(0)
	if signed {
		minimum = -storeMaxSafe
	}
	if err != nil || parsed < minimum || parsed > storeMaxSafe {
		return 0, storeBad("%s must be an integer in %d..%d", label, minimum, int64(storeMaxSafe))
	}
	return parsed, nil
}

func storeBool(value any, label string) (bool, error) {
	parsed, ok := value.(bool)
	if !ok {
		return false, storeBad("%s must be a boolean", label)
	}
	return parsed, nil
}

func storeText(value any, label string, maximum int) (string, error) {
	text, ok := value.(string)
	if !ok || len(text) > maximum || !utf8.ValidString(text) {
		return "", storeBad("%s must be bounded text", label)
	}
	for _, character := range text {
		if character < 0x20 || character == 0x7f {
			return "", storeBad("%s must be bounded text", label)
		}
	}
	return text, nil
}

func storeEnum(value any, allowed []string, label string) (string, error) {
	text, ok := value.(string)
	if !ok || !slices.Contains(allowed, text) {
		return "", storeBad("%s has unknown value %v", label, value)
	}
	return text, nil
}

func storeDigestValue(value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || !storeDigest.MatchString(text) {
		return "", storeBad("%s must be a sha256 digest", label)
	}
	return text, nil
}

func storeList(value any, label string, maximum int) ([]any, error) {
	list, ok := value.([]any)
	if !ok || len(list) > maximum {
		return nil, storeBad("%s must be a list of at most %d", label, maximum)
	}
	return list, nil
}

func parseWorkload(raw any, label string) (Workload, error) {
	value, err := storeObject(raw, label)
	if err != nil {
		return Workload{}, err
	}
	if err := storeKeys(value, []string{"task_class", "language", "size"}, nil, label); err != nil {
		return Workload{}, err
	}
	var workload Workload
	if workload.TaskClass, err = storeEnum(value["task_class"], storeTaskClasses, label+".task_class"); err != nil {
		return Workload{}, err
	}
	if workload.Language, err = storeEnum(value["language"], storeLanguages, label+".language"); err != nil {
		return Workload{}, err
	}
	if workload.Size, err = storeEnum(value["size"], storeSizes, label+".size"); err != nil {
		return Workload{}, err
	}
	return workload, nil
}

func storeCounts(value map[string]any, names []string, label string) (map[string]int64, error) {
	counts := make(map[string]int64, len(names))
	for _, name := range names {
		parsed, err := storeInt(value[name], label+"."+name, false)
		if err != nil {
			return nil, err
		}
		counts[name] = parsed
	}
	return counts, nil
}

func parseQuality(raw any, label string) (QualityEvidence, error) {
	value, err := storeObject(raw, label)
	if err != nil {
		return QualityEvidence{}, err
	}
	switch value["state"] {
	case "unmeasured":
		return QualityEvidence{}, storeKeys(value, []string{"state"}, nil, label)
	case "measured":
	default:
		return QualityEvidence{}, storeBad("%s.state has unknown value %v", label, value["state"])
	}
	if err := storeKeys(value, []string{"state", "retention", "recovery"}, nil, label); err != nil {
		return QualityEvidence{}, err
	}
	retention, err := storeObject(value["retention"], label+".retention")
	if err != nil {
		return QualityEvidence{}, err
	}
	retentionNames := []string{"retained", "recoverable", "lost"}
	if err := storeKeys(retention, retentionNames, nil, label+".retention"); err != nil {
		return QualityEvidence{}, err
	}
	recovery, err := storeObject(value["recovery"], label+".recovery")
	if err != nil {
		return QualityEvidence{}, err
	}
	recoveryNames := []string{"handles_emitted", "handles_verified", "failures", "critical_failures"}
	if err := storeKeys(recovery, recoveryNames, nil, label+".recovery"); err != nil {
		return QualityEvidence{}, err
	}
	kept, err := storeCounts(retention, retentionNames, label)
	if err != nil {
		return QualityEvidence{}, err
	}
	recovered, err := storeCounts(recovery, recoveryNames, label)
	if err != nil {
		return QualityEvidence{}, err
	}
	if recovered["handles_verified"] > recovered["handles_emitted"] ||
		recovered["failures"] > recovered["handles_emitted"] ||
		recovered["critical_failures"] > recovered["failures"] {
		return QualityEvidence{}, storeBad("%s has impossible recovery counts", label)
	}
	pointer := func(value int64) *int64 { return &value }
	return QualityEvidence{
		Measured:         true,
		Retained:         pointer(kept["retained"]),
		Recoverable:      pointer(kept["recoverable"]),
		Lost:             pointer(kept["lost"]),
		HandlesEmitted:   pointer(recovered["handles_emitted"]),
		HandlesVerified:  pointer(recovered["handles_verified"]),
		Failures:         pointer(recovered["failures"]),
		CriticalFailures: pointer(recovered["critical_failures"]),
	}, nil
}

func parseSecurity(raw any, label string) (SecurityEvidence, error) {
	value, err := storeObject(raw, label)
	if err != nil {
		return SecurityEvidence{}, err
	}
	switch value["state"] {
	case "unmeasured":
		return SecurityEvidence{}, storeKeys(value, []string{"state"}, nil, label)
	case "measured":
	default:
		return SecurityEvidence{}, storeBad("%s.state has unknown value %v", label, value["state"])
	}
	if err := storeKeys(value, []string{"state", "regressions"}, nil, label); err != nil {
		return SecurityEvidence{}, err
	}
	regressions, err := storeInt(value["regressions"], label, false)
	if err != nil {
		return SecurityEvidence{}, err
	}
	return SecurityEvidence{Measured: true, Regressions: &regressions}, nil
}

var (
	storeRecordCounts = []string{"observed_day", "samples", "accepted", "rejected", "explicit_overrides", "token_samples", "tokens_original", "tokens_delivered"}
	storeSignalCounts = []string{"signal_samples", "bounce_tasks", "expand_tasks", "edit_failure_tasks"}
)

func parseOutcomeRecord(raw any, label string) (StrategyOutcomeRecord, error) {
	value, err := storeObject(raw, label)
	if err != nil {
		return StrategyOutcomeRecord{}, err
	}
	required := append([]string{"workload", "strategy", "quality", "security"}, storeRecordCounts...)
	if err := storeKeys(value, required, storeSignalCounts, label); err != nil {
		return StrategyOutcomeRecord{}, err
	}
	counts, err := storeCounts(value, storeRecordCounts, label)
	if err != nil {
		return StrategyOutcomeRecord{}, err
	}
	// Signal counts default to zero, like the Engine's own deserializer.
	for _, name := range storeSignalCounts {
		raw, present := value[name]
		if !present {
			counts[name] = 0
			continue
		}
		if counts[name], err = storeInt(raw, label+"."+name, false); err != nil {
			return StrategyOutcomeRecord{}, err
		}
	}
	record := StrategyOutcomeRecord{
		ObservedDay: counts["observed_day"], Samples: counts["samples"], Accepted: counts["accepted"],
		Rejected: counts["rejected"], ExplicitOverrides: counts["explicit_overrides"],
		TokenSamples: counts["token_samples"], SignalSamples: counts["signal_samples"],
		BounceTasks: counts["bounce_tasks"], ExpandTasks: counts["expand_tasks"],
		EditFailureTasks: counts["edit_failure_tasks"], TokensOriginal: counts["tokens_original"],
		TokensDelivered: counts["tokens_delivered"],
	}
	if record.Workload, err = parseWorkload(value["workload"], label+".workload"); err != nil {
		return StrategyOutcomeRecord{}, err
	}
	if record.Strategy, err = storeEnum(value["strategy"], storeStrategies, label+".strategy"); err != nil {
		return StrategyOutcomeRecord{}, err
	}
	if record.Quality, err = parseQuality(value["quality"], label+".quality"); err != nil {
		return StrategyOutcomeRecord{}, err
	}
	if record.Security, err = parseSecurity(value["security"], label+".security"); err != nil {
		return StrategyOutcomeRecord{}, err
	}
	switch {
	case record.Samples == 0:
		return StrategyOutcomeRecord{}, storeBad("%s has no samples", label)
	case record.Accepted+record.Rejected != record.Samples:
		return StrategyOutcomeRecord{}, storeBad("%s: accepted + rejected must equal samples", label)
	case record.ExplicitOverrides > record.Samples || record.TokenSamples > record.Samples:
		return StrategyOutcomeRecord{}, storeBad("%s counts more tasks than samples", label)
	case record.SignalSamples > record.Samples || record.BounceTasks > record.SignalSamples ||
		record.ExpandTasks > record.SignalSamples || record.EditFailureTasks > record.SignalSamples:
		return StrategyOutcomeRecord{}, storeBad("%s signal counts exceed their attributed tasks", label)
	case record.TokenSamples == 0 && (record.TokensOriginal > 0 || record.TokensDelivered > 0):
		return StrategyOutcomeRecord{}, storeBad("%s has tokens without token samples", label)
	case record.TokensDelivered > record.TokensOriginal:
		return StrategyOutcomeRecord{}, storeBad("%s delivered more tokens than original", label)
	}
	return record, nil
}

func parseEvaluation(raw any, label string) (StrategyEvaluation, error) {
	value, err := storeObject(raw, label)
	if err != nil {
		return StrategyEvaluation{}, err
	}
	required := []string{"strategy", "evidence_tier", "verdict", "pairs", "powered", "delta_milli", "ci_low_milli", "ci_high_milli", "margin_milli"}
	if err := storeKeys(value, required, nil, label); err != nil {
		return StrategyEvaluation{}, err
	}
	var evaluation StrategyEvaluation
	if evaluation.Strategy, err = storeEnum(value["strategy"], storeStrategies, label+".strategy"); err != nil {
		return StrategyEvaluation{}, err
	}
	if evaluation.EvidenceTier, err = storeEnum(value["evidence_tier"], storeEvidenceTiers, label+".evidence_tier"); err != nil {
		return StrategyEvaluation{}, err
	}
	if evaluation.Verdict, err = storeEnum(value["verdict"], storeVerdicts, label+".verdict"); err != nil {
		return StrategyEvaluation{}, err
	}
	if evaluation.Pairs, err = storeInt(value["pairs"], label+".pairs", false); err != nil {
		return StrategyEvaluation{}, err
	}
	if evaluation.Powered, err = storeBool(value["powered"], label+".powered"); err != nil {
		return StrategyEvaluation{}, err
	}
	for name, target := range map[string]*int64{
		"delta_milli": &evaluation.DeltaMilli, "ci_low_milli": &evaluation.CILowMilli,
		"ci_high_milli": &evaluation.CIHighMilli, "margin_milli": &evaluation.MarginMilli,
	} {
		if *target, err = storeInt(value[name], label+"."+name, true); err != nil {
			return StrategyEvaluation{}, err
		}
	}
	switch {
	case evaluation.Strategy == "other":
		return StrategyEvaluation{}, storeBad("%s must name a known strategy", label)
	case evaluation.CILowMilli > evaluation.CIHighMilli || evaluation.MarginMilli < 0:
		return StrategyEvaluation{}, storeBad("%s has an impossible interval", label)
	case evaluation.Powered && evaluation.Pairs == 0:
		return StrategyEvaluation{}, storeBad("%s is powered without pairs", label)
	}
	return evaluation, nil
}

func recordKey(record StrategyOutcomeRecord) []int64 {
	return []int64{
		int64(slices.Index(storeTaskClasses, record.Workload.TaskClass)),
		int64(slices.Index(storeLanguages, record.Workload.Language)),
		int64(slices.Index(storeSizes, record.Workload.Size)),
		int64(slices.Index(storeStrategies, record.Strategy)),
		record.ObservedDay,
	}
}

// ParsePolicyEvidence parses and validates a ContextPolicyEvidenceV1 document.
func ParsePolicyEvidence(raw any) (*ContextPolicyEvidence, error) {
	value, err := storeObject(raw, "evidence")
	if err != nil {
		return nil, err
	}
	if err := storeKeys(value, []string{"schema_version", "records"}, []string{"evaluations"}, "evidence"); err != nil {
		return nil, err
	}
	if version, err := storeInt(value["schema_version"], "schema_version", false); err != nil || version != 1 {
		return nil, storeBad("unsupported evidence schema_version")
	}
	records, err := storeList(value["records"], "records", storeMaxRecords)
	if err != nil {
		return nil, err
	}
	evidence := &ContextPolicyEvidence{Records: []StrategyOutcomeRecord{}, Evaluations: []StrategyEvaluation{}}
	for index, item := range records {
		record, err := parseOutcomeRecord(item, fmt.Sprintf("records[%d]", index))
		if err != nil {
			return nil, err
		}
		if index > 0 && slices.Compare(recordKey(evidence.Records[index-1]), recordKey(record)) >= 0 {
			return nil, storeBad("records must be strictly sorted by workload, strategy and day")
		}
		evidence.Records = append(evidence.Records, record)
	}
	rawEvaluations, present := value["evaluations"]
	if !present {
		return evidence, nil
	}
	evaluations, err := storeList(rawEvaluations, "evaluations", len(storeStrategies))
	if err != nil {
		return nil, err
	}
	for index, item := range evaluations {
		evaluation, err := parseEvaluation(item, fmt.Sprintf("evaluations[%d]", index))
		if err != nil {
			return nil, err
		}
		if index > 0 && slices.Index(storeStrategies, evidence.Evaluations[index-1].Strategy) >= slices.Index(storeStrategies, evaluation.Strategy) {
			return nil, storeBad("evaluations must be one per strategy, sorted")
		}
		evidence.Evaluations = append(evidence.Evaluations, evaluation)
	}
	return evidence, nil
}

func parseStep(raw any, label string) (LineageStep, error) {
	value, err := storeObject(raw, label)
	if err != nil {
		return LineageStep{}, err
	}
	if err := storeKeys(value, []string{"sequence", "kind", "timestamp", "fields"}, nil, label); err != nil {
		return LineageStep{}, err
	}
	step := LineageStep{Fields: map[string]string{}}
	if step.Sequence, err = storeInt(value["sequence"], label+".sequence", false); err != nil {
		return LineageStep{}, err
	}
	if step.Kind, err = storeEnum(value["kind"], storeStepKinds, label+".kind"); err != nil {
		return LineageStep{}, err
	}
	if step.Timestamp, err = storeText(value["timestamp"], label+".timestamp", storeMaxRefBytes); err != nil {
		return LineageStep{}, err
	}
	fields, err := storeObject(value["fields"], label+".fields")
	if err != nil {
		return LineageStep{}, err
	}
	for key, item := range fields {
		if !storeField.MatchString(key) {
			return LineageStep{}, storeBad("%s.fields has an invalid name", label)
		}
		if step.Fields[key], err = storeText(item, label+".fields."+key, storeMaxRefBytes); err != nil {
			return LineageStep{}, err
		}
	}
	return step, nil
}

func storeOptionalDigest(value any, label string) (*string, error) {
	if value == nil {
		return nil, nil
	}
	digest, err := storeDigestValue(value, label)
	if err != nil {
		return nil, err
	}
	return &digest, nil
}

func parseDeliverySummary(raw any, label string) (*DeliverySummary, error) {
	value, err := storeObject(raw, label)
	if err != nil {
		return nil, err
	}
	counts := []string{"inspected", "delivered", "withheld", "redactions", "tokens_original", "tokens_delivered"}
	if err := storeKeys(value, append([]string{"outcome", "destination", "policy_digest", "final_context"}, counts...), nil, label); err != nil {
		return nil, err
	}
	parsed, err := storeCounts(value, counts, label)
	if err != nil {
		return nil, err
	}
	summary := &DeliverySummary{
		Inspected: parsed["inspected"], Delivered: parsed["delivered"], Withheld: parsed["withheld"],
		Redactions: parsed["redactions"], TokensOriginal: parsed["tokens_original"],
		TokensDelivered: parsed["tokens_delivered"],
	}
	if summary.Outcome, err = storeEnum(value["outcome"], storeDeliveryOutcomes, label+".outcome"); err != nil {
		return nil, err
	}
	if summary.Destination, err = storeText(value["destination"], label+".destination", storeMaxRefBytes); err != nil {
		return nil, err
	}
	if summary.PolicyDigest, err = storeOptionalDigest(value["policy_digest"], label+".policy_digest"); err != nil {
		return nil, err
	}
	if summary.FinalContext, err = storeOptionalDigest(value["final_context"], label+".final_context"); err != nil {
		return nil, err
	}
	if summary.TokensDelivered > summary.TokensOriginal {
		return nil, storeBad("%s delivered more tokens than original", label)
	}
	return summary, nil
}

func parseDelivery(raw any, label string) (LineageDelivery, error) {
	value, err := storeObject(raw, label)
	if err != nil {
		return LineageDelivery{}, err
	}
	if err := storeKeys(value, []string{"digest", "verified"}, []string{"error", "summary"}, label); err != nil {
		return LineageDelivery{}, err
	}
	var delivery LineageDelivery
	if delivery.Verified, err = storeBool(value["verified"], label+".verified"); err != nil {
		return LineageDelivery{}, err
	}
	_, hasSummary := value["summary"]
	rawError, hasError := value["error"]
	if delivery.Verified != hasSummary || delivery.Verified == hasError {
		return LineageDelivery{}, storeBad("%s: a verified delivery has a summary, an unverified one an error", label)
	}
	if delivery.Digest, err = storeDigestValue(value["digest"], label+".digest"); err != nil {
		return LineageDelivery{}, err
	}
	if hasError {
		reason, err := storeEnum(rawError, storeDeliveryErrors, label+".error")
		if err != nil {
			return LineageDelivery{}, err
		}
		delivery.Error = &reason
	}
	if delivery.Verified {
		if delivery.Summary, err = parseDeliverySummary(value["summary"], label+".summary"); err != nil {
			return LineageDelivery{}, err
		}
	}
	return delivery, nil
}

// ParseTaskLineage parses and validates a TaskLineageV1 document.
func ParseTaskLineage(raw any) (*TaskLineage, error) {
	value, err := storeObject(raw, "lineage")
	if err != nil {
		return nil, err
	}
	required := []string{"schema_version", "task_id", "steps", "deliveries", "outcome", "gaps"}
	if err := storeKeys(value, required, []string{"scope", "ledger_error"}, "lineage"); err != nil {
		return nil, err
	}
	if version, err := storeInt(value["schema_version"], "schema_version", false); err != nil || version != 1 {
		return nil, storeBad("unsupported lineage schema_version")
	}
	lineage := &TaskLineage{Steps: []LineageStep{}, Deliveries: []LineageDelivery{}, Gaps: []string{}}
	gaps, err := storeList(value["gaps"], "gaps", len(storeGaps))
	if err != nil {
		return nil, err
	}
	for _, item := range gaps {
		gap, err := storeEnum(item, storeGaps, "gaps")
		if err != nil {
			return nil, err
		}
		if slices.Contains(lineage.Gaps, gap) {
			return nil, storeBad("gaps must not repeat")
		}
		lineage.Gaps = append(lineage.Gaps, gap)
	}
	if raw, ok := value["ledger_error"]; ok {
		text, err := storeText(raw, "ledger_error", storeMaxTextBytes)
		if err != nil {
			return nil, err
		}
		lineage.LedgerError = &text
	}
	if (lineage.LedgerError != nil) != slices.Contains(lineage.Gaps, "ledger_unverified") {
		return nil, storeBad("a ledger error and the ledger_unverified gap go together")
	}
	steps, err := storeList(value["steps"], "steps", storeMaxLineageItems)
	if err != nil {
		return nil, err
	}
	for index, item := range steps {
		step, err := parseStep(item, fmt.Sprintf("steps[%d]", index))
		if err != nil {
			return nil, err
		}
		lineage.Steps = append(lineage.Steps, step)
	}
	deliveries, err := storeList(value["deliveries"], "deliveries", storeMaxLineageItems)
	if err != nil {
		return nil, err
	}
	unverified := false
	for index, item := range deliveries {
		delivery, err := parseDelivery(item, fmt.Sprintf("deliveries[%d]", index))
		if err != nil {
			return nil, err
		}
		unverified = unverified || !delivery.Verified
		lineage.Deliveries = append(lineage.Deliveries, delivery)
	}
	if unverified != slices.Contains(lineage.Gaps, "delivery_unverified") {
		return nil, storeBad("an unverified delivery and the delivery_unverified gap go together")
	}
	if lineage.TaskID, err = storeText(value["task_id"], "task_id", storeMaxRefBytes); err != nil {
		return nil, err
	}
	if raw, ok := value["scope"]; ok {
		scope, err := storeText(raw, "scope", storeMaxTextBytes)
		if err != nil {
			return nil, err
		}
		lineage.Scope = &scope
	}
	if lineage.Outcome, err = storeEnum(value["outcome"], storeOutcomes, "outcome"); err != nil {
		return nil, err
	}
	return lineage, nil
}

func storeRequest(scope ContextStoreScope, taskID string) ([]byte, error) {
	request := map[string]any{"schema_version": 1, "transport_version": 1, "engine_interface_version": "1.0.0"}
	for key, item := range map[string]string{"project_id": scope.ProjectID, "tenant_id": scope.TenantID, "task_id": taskID} {
		if item == "" {
			continue
		}
		if strings.TrimSpace(item) == "" || len(item) > storeMaxRefBytes || !utf8.ValidString(item) {
			return nil, NewValidationError(key + " must be a non-empty bounded string")
		}
		request[key] = item
	}
	return json.Marshal(request)
}

func (c *SubprocessEngineClient) storeRead(parent context.Context, operation, projectRoot string, payload []byte, body string) (any, error) {
	if err := c.configured(); err != nil {
		return nil, err
	}
	root, err := validateRoot(projectRoot)
	if err != nil {
		return nil, err
	}
	if len(payload) > maxStoreRequestBytes {
		return nil, NewValidationError("context store request exceeds its bound")
	}
	raw, err := c.runPayload(parent, operation, root, payload)
	if err != nil {
		return nil, err
	}
	if len(raw) > maxStoreResponseBytes {
		return nil, storeBad("response exceeds its bound")
	}
	document, err := strictJSONLoads(raw, "context store")
	if err != nil {
		return nil, storeBad("response is not strict JSON")
	}
	value, err := storeObject(document, "response")
	if err != nil {
		return nil, err
	}
	if err := storeKeys(value, []string{"schema_version", "transport_version", "engine_interface_version", body}, nil, "response"); err != nil {
		return nil, err
	}
	schema, schemaErr := storeInt(value["schema_version"], "schema_version", false)
	transport, transportErr := storeInt(value["transport_version"], "transport_version", false)
	if schemaErr != nil || transportErr != nil || schema != 1 || transport != 1 || value["engine_interface_version"] != "1.0.0" {
		return nil, storeBad("unsupported response versions")
	}
	return value[body], nil
}

// ReadPolicyEvidence returns the scope's read-strategy evidence.
func (c *SubprocessEngineClient) ReadPolicyEvidence(parent context.Context, projectRoot string, scope ContextStoreScope) (*ContextPolicyEvidence, error) {
	payload, err := storeRequest(scope, "")
	if err != nil {
		return nil, err
	}
	body, err := c.storeRead(parent, "context-policy-evidence", projectRoot, payload, "evidence")
	if err != nil {
		return nil, err
	}
	return ParsePolicyEvidence(body)
}

// ReadTaskLineage returns one task's lineage within the scope.
func (c *SubprocessEngineClient) ReadTaskLineage(parent context.Context, projectRoot, taskID string, scope ContextStoreScope) (*TaskLineage, error) {
	if strings.TrimSpace(taskID) == "" {
		return nil, NewValidationError("task id must be a non-empty string")
	}
	payload, err := storeRequest(scope, taskID)
	if err != nil {
		return nil, err
	}
	body, err := c.storeRead(parent, "context-lineage", projectRoot, payload, "lineage")
	if err != nil {
		return nil, err
	}
	return ParseTaskLineage(body)
}
