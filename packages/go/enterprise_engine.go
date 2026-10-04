// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package leanctx

import (
	"context"
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const (
	enterpriseContextPlanPath    = "/v1/engine/context-plan"
	enterpriseSchemaVersion      = uint64(1)
	enterpriseTransportVersion   = uint64(1)
	enterpriseMaxRequestBytes    = 64 * 1024
	enterpriseMaxResponseBytes   = 1024 * 1024
	enterpriseMaxPlanItems       = 256
	enterpriseMaxIdentifierBytes = 256
	enterpriseMaxReferenceBytes  = 1024
	enterpriseMaxQueryBytes      = 16 * 1024
	enterpriseMaxPlanTokens      = uint64(1_048_576)
	enterpriseMaxPlanCandidates  = uint64(256)
	enterpriseMaxExtensionBytes  = 64 * 1024
	enterpriseMaxExtensionDepth  = 8
)

var enterpriseTimestampPattern = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`)

// EnginePlanningRequest is the canonical, non-executing Engine planning request.
type EnginePlanningRequest struct {
	TaskID        string
	Query         string
	BudgetTokens  uint64
	MaxCandidates uint64
}

// NewEnginePlanningRequest validates and returns a canonical planning request.
// A zero MaxCandidates selects the protocol default of 64.
func NewEnginePlanningRequest(taskID, query string, budgetTokens, maxCandidates uint64) (EnginePlanningRequest, error) {
	request := EnginePlanningRequest{
		TaskID:        taskID,
		Query:         query,
		BudgetTokens:  budgetTokens,
		MaxCandidates: maxCandidates,
	}
	if request.MaxCandidates == 0 {
		request.MaxCandidates = 64
	}
	if err := request.validate(); err != nil {
		return EnginePlanningRequest{}, err
	}
	return request, nil
}

func (r EnginePlanningRequest) normalized() EnginePlanningRequest {
	if r.MaxCandidates == 0 {
		r.MaxCandidates = 64
	}
	return r
}

func (r EnginePlanningRequest) validate() error {
	r = r.normalized()
	if err := enterpriseText(r.TaskID, "task_id", enterpriseMaxIdentifierBytes, true, true); err != nil {
		return err
	}
	if err := enterpriseText(r.Query, "query", enterpriseMaxQueryBytes, false, true); err != nil {
		return err
	}
	if r.BudgetTokens < 1 || r.BudgetTokens > enterpriseMaxPlanTokens {
		return NewValidationError("budget_tokens is outside its protocol bounds")
	}
	if r.MaxCandidates < 1 || r.MaxCandidates > enterpriseMaxPlanCandidates {
		return NewValidationError("max_candidates is outside its protocol bounds")
	}
	return nil
}

// ToDict returns the detached canonical wire projection.
func (r EnginePlanningRequest) ToDict() map[string]any {
	r = r.normalized()
	return map[string]any{
		"schema_version":           int64(enterpriseSchemaVersion),
		"transport_version":        int64(enterpriseTransportVersion),
		"engine_interface_version": EngineInterfaceVersion,
		"task_id":                  r.TaskID,
		"query":                    r.Query,
		"budget_tokens":            r.BudgetTokens,
		"max_candidates":           r.MaxCandidates,
	}
}

// EngineSourcePlanResponse is a validated tenant-bound source plan.
// RawResponse is the detached strict-JSON response map; it is not a receipt or
// execution proof and must not be treated as an authorization decision.
type EngineSourcePlanResponse struct {
	SchemaVersion      uint64
	TenantID           string
	GovernanceRevision uint64
	Plan               map[string]any
	RawResponse        map[string]any
}

// EnterpriseEngineClient calls the authenticated Enterprise source-planning API.
type EnterpriseEngineClient struct {
	transport *EngineContextClient
	tenantID  string
}

// NewEnterpriseEngineClient constructs a bounded authenticated Enterprise client.
func NewEnterpriseEngineClient(baseURL, credential, tenantID string, options ...EngineContextClientOptions) (*EnterpriseEngineClient, error) {
	transport, err := NewEngineContextClient(baseURL, credential, options...)
	if err != nil {
		return nil, err
	}
	checkedTenant, err := enterpriseUUID(tenantID, "tenant_id")
	if err != nil {
		return nil, err
	}
	return &EnterpriseEngineClient{transport: transport, tenantID: checkedTenant}, nil
}

// TenantID returns the normalized configured tenant identifier.
func (c *EnterpriseEngineClient) TenantID() string {
	if c == nil {
		return ""
	}
	return c.tenantID
}

// ContextPlanSources executes one bounded authenticated source-plan request.
func (c *EnterpriseEngineClient) ContextPlanSources(request EnginePlanningRequest, sourceIDs []string) (*EngineSourcePlanResponse, error) {
	return c.ContextPlanSourcesContext(context.Background(), request, sourceIDs)
}

// ContextPlanSourcesContext is the cancellable form of ContextPlanSources.
func (c *EnterpriseEngineClient) ContextPlanSourcesContext(parent context.Context, request EnginePlanningRequest, sourceIDs []string) (*EngineSourcePlanResponse, error) {
	if c == nil || c.transport == nil {
		return nil, NewConfigurationError("EnterpriseEngineClient is not initialized")
	}
	request = request.normalized()
	if err := request.validate(); err != nil {
		return nil, err
	}
	normalizedIDs, err := enterpriseSourceIDs(sourceIDs)
	if err != nil {
		return nil, err
	}
	payload, err := canonicalJSON(map[string]any{
		"planning":   request.ToDict(),
		"source_ids": normalizedIDs,
	})
	if err != nil {
		return nil, NewValidationError("Enterprise Engine planning request is not canonical JSON")
	}
	if len(payload) > enterpriseMaxRequestBytes {
		return nil, NewValidationError("Enterprise Engine planning request exceeds its byte bound")
	}
	raw, err := c.transport.postJSONContext(parent, enterpriseContextPlanPath, payload, enterpriseMaxResponseBytes, "Enterprise Engine context-plan", "planning request")
	if err != nil {
		return nil, err
	}
	return c.parseResponse(raw, request, normalizedIDs)
}

func (c *EnterpriseEngineClient) parseResponse(raw []byte, request EnginePlanningRequest, sourceIDs []string) (*EngineSourcePlanResponse, error) {
	if !contextReadUnicodeEscapesValid(raw) {
		return nil, enterpriseProtocol("Enterprise Engine context-plan response has an unpaired Unicode surrogate")
	}
	decoded, err := strictJSONLoads(raw, "Enterprise Engine context-plan response")
	if err != nil {
		return nil, NewEngineProtocolError("Enterprise Engine context-plan response is not valid JSON")
	}
	wrapper, err := enterpriseObject(decoded, "Enterprise Engine context-plan response")
	if err != nil {
		return nil, err
	}
	if err := enterpriseExactKeys(wrapper, map[string]bool{
		"schema_version": true, "tenant_id": true, "governance_revision": true, "plan": true,
	}, "Enterprise Engine context-plan response"); err != nil {
		return nil, err
	}
	schemaVersion, err := enterpriseU64(wrapper["schema_version"], "response.schema_version")
	if err != nil || schemaVersion != enterpriseSchemaVersion {
		return nil, enterpriseProtocol("response.schema_version is unsupported")
	}
	tenantID, err := enterpriseUUIDValue(wrapper["tenant_id"], "response.tenant_id")
	if err != nil {
		return nil, err
	}
	if tenantID != c.tenantID {
		return nil, enterpriseProtocol("Enterprise Engine response tenant binding does not match")
	}
	governanceRevision, err := enterpriseU64(wrapper["governance_revision"], "response.governance_revision")
	if err != nil {
		return nil, err
	}
	planValue, err := enterpriseObject(wrapper["plan"], "response.plan")
	if err != nil {
		return nil, err
	}
	plan, err := enterpriseSourcePlan(planValue, request)
	if err != nil {
		return nil, err
	}
	if err := enterpriseSourceScope(plan, sourceIDs); err != nil {
		return nil, err
	}
	return &EngineSourcePlanResponse{
		SchemaVersion:      schemaVersion,
		TenantID:           tenantID,
		GovernanceRevision: governanceRevision,
		Plan:               plan,
		RawResponse:        wrapper,
	}, nil
}

func enterpriseSourceIDs(values []string) ([]string, error) {
	if len(values) > 64 {
		return nil, NewValidationError("source_ids exceeds the Engine source bound")
	}
	result := make([]string, len(values))
	seen := make(map[string]bool, len(values))
	for index, value := range values {
		normalized, err := enterpriseUUID(value, "source_id")
		if err != nil {
			return nil, err
		}
		if seen[normalized] {
			return nil, NewValidationError("source_ids must not contain duplicates")
		}
		seen[normalized] = true
		result[index] = normalized
	}
	return result, nil
}

func enterpriseUUID(value, field string) (string, error) {
	if value == "" || len(value) != 36 || value != strings.TrimSpace(value) {
		return "", NewValidationError(field + " must be a canonical UUID")
	}
	lower := strings.ToLower(value)
	for index, character := range lower {
		if index == 8 || index == 13 || index == 18 || index == 23 {
			if character != '-' {
				return "", NewValidationError(field + " must be a canonical UUID")
			}
			continue
		}
		if !((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')) {
			return "", NewValidationError(field + " must be a canonical UUID")
		}
	}
	compact := strings.ReplaceAll(lower, "-", "")
	if strings.Trim(compact, "0") == "" {
		return "", NewValidationError(field + " must be a non-nil canonical UUID")
	}
	return lower, nil
}

func enterpriseUUIDValue(value any, field string) (string, error) {
	text, ok := value.(string)
	if !ok {
		return "", enterpriseProtocol(field + " must be a canonical UUID")
	}
	normalized, err := enterpriseUUID(text, field)
	if err != nil {
		return "", enterpriseProtocol(field + " must be a non-nil canonical UUID")
	}
	return normalized, nil
}

func enterpriseText(value, field string, maximum int, controls, nonblank bool) error {
	if !utf8.ValidString(value) {
		return NewValidationError(field + " is not valid UTF-8")
	}
	if value == "" || len([]byte(value)) > maximum {
		return NewValidationError(field + " exceeds its UTF-8 byte bound")
	}
	if strings.IndexByte(value, 0) >= 0 {
		return NewValidationError(field + " contains NUL")
	}
	if controls {
		for _, character := range value {
			if unicode.IsControl(character) {
				return NewValidationError(field + " contains a control character")
			}
		}
	}
	if nonblank && strings.TrimSpace(value) == "" {
		return NewValidationError(field + " must not be blank")
	}
	return nil
}

func enterpriseProtocol(message string) error {
	return NewEngineProtocolError(message)
}

func enterpriseObject(value any, field string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, enterpriseProtocol(field + " must be an object")
	}
	return object, nil
}

func enterpriseExactKeys(value map[string]any, expected map[string]bool, field string) error {
	if len(value) != len(expected) {
		return enterpriseProtocol(field + " fields do not match the v1 contract")
	}
	for key := range value {
		if !expected[key] {
			return enterpriseProtocol(field + " fields do not match the v1 contract")
		}
	}
	return nil
}

func enterpriseAllowedKeys(value map[string]any, allowed, required map[string]bool, field string) error {
	for key := range value {
		if !allowed[key] {
			return enterpriseProtocol(field + " fields do not match the v1 contract")
		}
	}
	for key := range required {
		if _, ok := value[key]; !ok {
			return enterpriseProtocol(field + " is missing a required field")
		}
	}
	return nil
}

func enterpriseU64(value any, field string) (uint64, error) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, enterpriseProtocol(field + " must be an unsigned integer")
	}
	lexical := number.String()
	if lexical == "" || (len(lexical) > 1 && lexical[0] == '0') {
		return 0, enterpriseProtocol(field + " must be an unsigned integer")
	}
	for _, character := range lexical {
		if character < '0' || character > '9' {
			return 0, enterpriseProtocol(field + " must be an unsigned integer")
		}
	}
	parsed, parseErr := strconv.ParseUint(lexical, 10, 64)
	if parseErr != nil {
		return 0, enterpriseProtocol(field + " must be an unsigned integer")
	}
	return parsed, nil
}

func enterpriseString(value any, field string, maximum int, nonblank bool) (string, error) {
	text, ok := value.(string)
	if !ok {
		return "", enterpriseProtocol(field + " must be a string")
	}
	if err := enterpriseText(text, field, maximum, true, nonblank); err != nil {
		return "", enterpriseProtocol(err.Error())
	}
	return text, nil
}

func enterpriseIdentifier(value any, field string) (string, error) {
	return enterpriseString(value, field, enterpriseMaxIdentifierBytes, true)
}

func enterpriseReference(value any, field string) (string, error) {
	return enterpriseString(value, field, enterpriseMaxReferenceBytes, true)
}

func enterpriseEnum(value any, field string, allowed map[string]bool) (string, error) {
	text, err := enterpriseString(value, field, enterpriseMaxIdentifierBytes, true)
	if err != nil {
		return "", err
	}
	if !allowed[text] {
		return "", enterpriseProtocol(field + " has an unsupported value")
	}
	return text, nil
}

func enterpriseDigest(value any, field string) (string, error) {
	text, ok := value.(string)
	if !ok {
		return "", enterpriseProtocol(field + " must be a digest")
	}
	if err := validateDigest(text, field); err != nil {
		return "", enterpriseProtocol(err.Error())
	}
	return text, nil
}

func enterpriseTimestamp(value any, field string) (string, error) {
	text, err := enterpriseString(value, field, enterpriseMaxIdentifierBytes, true)
	if err != nil {
		return "", err
	}
	if !enterpriseTimestampPattern.MatchString(text) {
		return "", enterpriseProtocol(field + " must use canonical UTC timestamp syntax")
	}
	if _, parseErr := time.Parse("2006-01-02T15:04:05Z", text); parseErr != nil {
		return "", enterpriseProtocol(field + " contains an invalid date or time")
	}
	return text, nil
}

func enterpriseOptionalString(value any, field string, maximum int) (any, error) {
	if value == nil {
		return nil, nil
	}
	text, err := enterpriseString(value, field, maximum, true)
	if err != nil {
		return nil, err
	}
	return text, nil
}

func enterpriseProjectionDigest(value any, field string) (string, error) {
	return enterpriseDigest(value, field)
}

var (
	enterpriseSourceTypes     = map[string]bool{"filesystem": true, "issue_tracker": true, "relational_database": true, "other": true}
	enterprisePermissions     = map[string]bool{"permitted": true, "denied": true, "unknown": true}
	enterpriseClassifications = map[string]bool{"Public": true, "Internal": true, "Confidential": true, "Restricted": true}
	enterpriseDispositions    = map[string]bool{"selected": true, "excluded": true, "deferred": true}
	enterpriseReasonCodes     = map[string]bool{
		"relevant": true, "required": true, "cache_hit": true, "budget_exceeded": true,
		"lower_utility": true, "policy_excluded": true, "deferred_for_later": true, "other": true,
	}
	enterpriseEvidenceKinds = map[string]bool{
		"ProviderReceipt": true, "RuntimeLog": true, "SignedBatch": true,
		"QualityMeasurement": true, "ExperimentOutcome": true,
	}
	enterpriseSignatureStatuses = map[string]bool{"Verified": true, "Unverified": true, "NotSigned": true}
)

var (
	enterprisePlanKeys = map[string]bool{
		"schema_version": true, "context_plan_id": true, "task_id": true, "projection_digest": true,
		"budget_tokens": true, "selections": true, "provider_stats": true,
		"policy_decision_refs": true, "evidence": true,
	}
	enterpriseSelectionKeys = map[string]bool{
		"source_ref": true, "provider": true, "disposition": true, "token_count": true,
		"sha256_digest": true, "reason_codes": true, "reason_detail": true,
	}
	enterpriseProviderStatsKeys = map[string]bool{"candidates_offered": true, "candidates_selected": true, "tokens_used": true}
	enterpriseEvidenceKeys      = map[string]bool{
		"schema_version": true, "kind": true, "uri": true, "digest": true,
		"signature_status": true, "media_type": true,
	}
	enterpriseDescriptorKeys = map[string]bool{
		"object_ref": true, "source_id": true, "source_type": true, "content_digest": true,
		"revision": true, "owner": true, "observed_at": true, "valid_until": true,
		"classification": true, "permission": true,
	}
)

func enterpriseExtensionValue(value any, depth int) error {
	if depth > enterpriseMaxExtensionDepth {
		return enterpriseProtocol("extension value exceeds its nesting bound")
	}
	switch nested := value.(type) {
	case map[string]any:
		if len(nested) > enterpriseMaxPlanItems {
			return enterpriseProtocol("extension object exceeds its item bound")
		}
		for key, child := range nested {
			if err := enterpriseText(key, "extension object key", enterpriseMaxIdentifierBytes, true, true); err != nil {
				return enterpriseProtocol(err.Error())
			}
			if err := enterpriseExtensionValue(child, depth+1); err != nil {
				return err
			}
		}
	case []any:
		if len(nested) > enterpriseMaxPlanItems {
			return enterpriseProtocol("extension array exceeds its item bound")
		}
		for _, child := range nested {
			if err := enterpriseExtensionValue(child, depth+1); err != nil {
				return err
			}
		}
	case string:
		if !utf8.ValidString(nested) || len([]byte(nested)) > enterpriseMaxExtensionBytes {
			return enterpriseProtocol("extension string exceeds its byte bound")
		}
	case json.Number:
		if _, numberErr := canonicalJSON(nested); numberErr != nil {
			return enterpriseProtocol("extension number is outside the canonical JSON number domain")
		}
	case nil, bool:
	default:
		return enterpriseProtocol("extension value is not canonical JSON")
	}
	encoded, err := json.Marshal(value)
	if err != nil || len(encoded) > enterpriseMaxExtensionBytes {
		return enterpriseProtocol("extension value exceeds its serialized byte bound")
	}
	return nil
}

func enterpriseExtensions(value map[string]any, reserved map[string]bool) error {
	count := 0
	for key, nested := range value {
		if reserved[key] {
			continue
		}
		count++
		if err := enterpriseText(key, "extension key", enterpriseMaxIdentifierBytes, true, true); err != nil {
			return enterpriseProtocol(err.Error())
		}
		if err := enterpriseExtensionValue(nested, 0); err != nil {
			return err
		}
	}
	if count > enterpriseMaxPlanItems {
		return enterpriseProtocol("extensions exceed their field bound")
	}
	return nil
}

func enterpriseDescriptor(value any, field string) (map[string]any, error) {
	raw, err := enterpriseObject(value, field)
	if err != nil {
		return nil, err
	}
	if err := enterpriseAllowedKeys(raw, enterpriseDescriptorKeys, map[string]bool{
		"object_ref": true, "source_id": true, "source_type": true, "content_digest": true,
	}, field); err != nil {
		return nil, err
	}
	objectRef, err := enterpriseReference(raw["object_ref"], field+".object_ref")
	if err != nil {
		return nil, err
	}
	sourceID, err := enterpriseIdentifier(raw["source_id"], field+".source_id")
	if err != nil {
		return nil, err
	}
	sourceType, err := enterpriseEnum(raw["source_type"], field+".source_type", enterpriseSourceTypes)
	if err != nil {
		return nil, err
	}
	contentDigest, err := enterpriseDigest(raw["content_digest"], field+".content_digest")
	if err != nil {
		return nil, err
	}
	result := map[string]any{
		"object_ref": objectRef, "source_id": sourceID, "source_type": sourceType,
		"content_digest": contentDigest, "revision": nil, "owner": nil,
		"observed_at": nil, "valid_until": nil, "classification": nil, "permission": "unknown",
	}
	for _, item := range []struct {
		key string
		max int
	}{
		{"revision", enterpriseMaxReferenceBytes}, {"owner", enterpriseMaxReferenceBytes},
	} {
		if value, present := raw[item.key]; present && value != nil {
			text, textErr := enterpriseOptionalString(value, field+"."+item.key, item.max)
			if textErr != nil {
				return nil, textErr
			}
			result[item.key] = text
		}
	}
	for _, item := range []string{"observed_at", "valid_until"} {
		if value, present := raw[item]; present && value != nil {
			timestamp, timestampErr := enterpriseTimestamp(value, field+"."+item)
			if timestampErr != nil {
				return nil, timestampErr
			}
			result[item] = timestamp
		}
	}
	if value, present := raw["classification"]; present && value != nil {
		classification, classificationErr := enterpriseEnum(value, field+".classification", enterpriseClassifications)
		if classificationErr != nil {
			return nil, classificationErr
		}
		result["classification"] = classification
	}
	if value, present := raw["permission"]; present && value != nil {
		permission, permissionErr := enterpriseEnum(value, field+".permission", enterprisePermissions)
		if permissionErr != nil {
			return nil, permissionErr
		}
		result["permission"] = permission
	}
	observed, _ := result["observed_at"].(string)
	validUntil, _ := result["valid_until"].(string)
	if observed != "" && validUntil != "" && validUntil <= observed {
		return nil, enterpriseProtocol(field + " validity window is inverted")
	}
	return result, nil
}

func enterpriseSelection(value any, field string) (map[string]any, error) {
	raw, err := enterpriseObject(value, field)
	if err != nil {
		return nil, err
	}
	if err := enterpriseAllowedKeys(raw, enterpriseSelectionKeys, map[string]bool{
		"source_ref": true, "provider": true, "disposition": true, "token_count": true, "reason_codes": true,
	}, field); err != nil {
		return nil, err
	}
	sourceRef, err := enterpriseIdentifier(raw["source_ref"], field+".source_ref")
	if err != nil {
		return nil, err
	}
	provider, err := enterpriseIdentifier(raw["provider"], field+".provider")
	if err != nil {
		return nil, err
	}
	disposition, err := enterpriseEnum(raw["disposition"], field+".disposition", enterpriseDispositions)
	if err != nil {
		return nil, err
	}
	tokenCount, err := enterpriseU64(raw["token_count"], field+".token_count")
	if err != nil {
		return nil, err
	}
	reasonsRaw, ok := raw["reason_codes"].([]any)
	if !ok || len(reasonsRaw) == 0 || len(reasonsRaw) > enterpriseMaxPlanItems {
		return nil, enterpriseProtocol(field + ".reason_codes has an invalid shape")
	}
	reasons := make([]any, len(reasonsRaw))
	seenReasons := make(map[string]bool, len(reasonsRaw))
	for index, reasonValue := range reasonsRaw {
		reason, reasonErr := enterpriseEnum(reasonValue, field+".reason_codes", enterpriseReasonCodes)
		if reasonErr != nil {
			return nil, reasonErr
		}
		if seenReasons[reason] {
			return nil, enterpriseProtocol(field + ".reason_codes contains duplicates")
		}
		seenReasons[reason] = true
		reasons[index] = reason
	}
	result := map[string]any{
		"source_ref": sourceRef, "provider": provider, "disposition": disposition,
		"token_count": tokenCount, "reason_codes": reasons,
	}
	if value, present := raw["sha256_digest"]; present && value != nil {
		digest, digestErr := enterpriseProjectionDigest(value, field+".sha256_digest")
		if digestErr != nil {
			return nil, digestErr
		}
		result["sha256_digest"] = digest
	}
	if value, present := raw["reason_detail"]; present && value != nil {
		detail, detailErr := enterpriseIdentifier(value, field+".reason_detail")
		if detailErr != nil {
			return nil, detailErr
		}
		result["reason_detail"] = detail
	}
	return result, nil
}

func enterprisePlan(value any) (map[string]any, error) {
	raw, err := enterpriseObject(value, "plan")
	if err != nil {
		return nil, err
	}
	if err := enterpriseExtensions(raw, enterprisePlanKeys); err != nil {
		return nil, err
	}
	for key := range map[string]bool{
		"schema_version": true, "context_plan_id": true, "task_id": true,
		"budget_tokens": true, "selections": true,
	} {
		if _, present := raw[key]; !present {
			return nil, enterpriseProtocol("plan is missing a required field")
		}
	}
	schemaVersion, err := enterpriseU64(raw["schema_version"], "plan.schema_version")
	if err != nil || schemaVersion != enterpriseSchemaVersion {
		return nil, enterpriseProtocol("plan.schema_version is unsupported")
	}
	selectionValues, ok := raw["selections"].([]any)
	if !ok || len(selectionValues) > enterpriseMaxPlanItems {
		return nil, enterpriseProtocol("plan.selections has an invalid shape")
	}
	selections := make([]any, len(selectionValues))
	refs := make(map[string]bool, len(selectionValues))
	var selectedTokens uint64
	for index, selectionValue := range selectionValues {
		selection, selectionErr := enterpriseSelection(selectionValue, "plan.selections["+strconv.Itoa(index)+"]")
		if selectionErr != nil {
			return nil, selectionErr
		}
		ref := selection["source_ref"].(string)
		if refs[ref] {
			return nil, enterpriseProtocol("plan.selections contains duplicate source_ref values")
		}
		refs[ref] = true
		if selection["disposition"] == "selected" {
			tokens := selection["token_count"].(uint64)
			if tokens > ^uint64(0)-selectedTokens {
				return nil, enterpriseProtocol("plan selected context exceeds budget_tokens")
			}
			selectedTokens += tokens
		}
		selections[index] = selection
	}
	budget, err := enterpriseU64(raw["budget_tokens"], "plan.budget_tokens")
	if err != nil {
		return nil, err
	}
	if selectedTokens > budget {
		return nil, enterpriseProtocol("plan selected context exceeds budget_tokens")
	}
	contextPlanID, err := enterpriseIdentifier(raw["context_plan_id"], "plan.context_plan_id")
	if err != nil {
		return nil, err
	}
	taskID, err := enterpriseIdentifier(raw["task_id"], "plan.task_id")
	if err != nil {
		return nil, err
	}
	result := map[string]any{
		"schema_version": enterpriseSchemaVersion, "context_plan_id": contextPlanID,
		"task_id": taskID, "budget_tokens": budget, "selections": selections,
	}
	if value, present := raw["projection_digest"]; present && value != nil {
		digest, digestErr := enterpriseDigest(value, "plan.projection_digest")
		if digestErr != nil {
			return nil, digestErr
		}
		result["projection_digest"] = digest
	}
	if value, present := raw["provider_stats"]; present {
		stats, statsErr := enterpriseProviderStats(value)
		if statsErr != nil {
			return nil, statsErr
		}
		if len(stats) > 0 {
			result["provider_stats"] = stats
		}
	}
	if value, present := raw["policy_decision_refs"]; present {
		refsValue, refsOK := value.([]any)
		if !refsOK || len(refsValue) > enterpriseMaxPlanItems {
			return nil, enterpriseProtocol("plan.policy_decision_refs has an invalid shape")
		}
		normalizedRefs := make([]any, len(refsValue))
		seen := make(map[string]bool, len(refsValue))
		for index, refValue := range refsValue {
			ref, refErr := enterpriseIdentifier(refValue, "plan.policy_decision_refs["+strconv.Itoa(index)+"]")
			if refErr != nil {
				return nil, refErr
			}
			if seen[ref] {
				return nil, enterpriseProtocol("plan.policy_decision_refs contains duplicates")
			}
			seen[ref] = true
			normalizedRefs[index] = ref
		}
		if len(normalizedRefs) > 0 {
			result["policy_decision_refs"] = normalizedRefs
		}
	}
	if value, present := raw["evidence"]; present {
		evidence, evidenceErr := enterpriseEvidence(value)
		if evidenceErr != nil {
			return nil, evidenceErr
		}
		if len(evidence) > 0 {
			result["evidence"] = evidence
		}
	}
	for key, nested := range raw {
		if !enterprisePlanKeys[key] {
			result[key] = nested
		}
	}
	if digest, present := result["projection_digest"]; present {
		unsigned := make(map[string]any, len(result)-1)
		for key, nested := range result {
			if key != "projection_digest" {
				unsigned[key] = nested
			}
		}
		expected, digestErr := canonicalDigest(unsigned)
		if digestErr != nil || digest != expected {
			return nil, enterpriseProtocol("plan.projection_digest does not match canonical projection content")
		}
	}
	return result, nil
}

func enterpriseProviderStats(value any) (map[string]any, error) {
	raw, err := enterpriseObject(value, "plan.provider_stats")
	if err != nil {
		return nil, err
	}
	if len(raw) > enterpriseMaxPlanItems {
		return nil, enterpriseProtocol("plan.provider_stats exceeds its item bound")
	}
	result := make(map[string]any, len(raw))
	for provider, value := range raw {
		if err := enterpriseText(provider, "plan.provider_stats key", enterpriseMaxIdentifierBytes, true, true); err != nil {
			return nil, enterpriseProtocol(err.Error())
		}
		stats, statsErr := enterpriseObject(value, "plan.provider_stats entry")
		if statsErr != nil {
			return nil, statsErr
		}
		if err := enterpriseExactKeys(stats, enterpriseProviderStatsKeys, "plan.provider_stats entry"); err != nil {
			return nil, err
		}
		offered, offeredErr := enterpriseU64(stats["candidates_offered"], "plan.provider_stats.candidates_offered")
		if offeredErr != nil {
			return nil, offeredErr
		}
		selected, selectedErr := enterpriseU64(stats["candidates_selected"], "plan.provider_stats.candidates_selected")
		if selectedErr != nil {
			return nil, selectedErr
		}
		if selected > offered {
			return nil, enterpriseProtocol("plan.provider_stats selected exceeds offered")
		}
		tokens, tokensErr := enterpriseU64(stats["tokens_used"], "plan.provider_stats.tokens_used")
		if tokensErr != nil {
			return nil, tokensErr
		}
		result[provider] = map[string]any{
			"candidates_offered": offered, "candidates_selected": selected, "tokens_used": tokens,
		}
	}
	return result, nil
}

func enterpriseEvidence(value any) ([]any, error) {
	values, ok := value.([]any)
	if !ok || len(values) > enterpriseMaxPlanItems {
		return nil, enterpriseProtocol("plan.evidence has an invalid shape")
	}
	result := make([]any, 0, len(values))
	for index, item := range values {
		raw, err := enterpriseObject(item, "plan.evidence["+strconv.Itoa(index)+"]")
		if err != nil {
			return nil, err
		}
		if err := enterpriseExtensions(raw, enterpriseEvidenceKeys); err != nil {
			return nil, err
		}
		for key := range map[string]bool{"kind": true, "uri": true, "digest": true, "signature_status": true} {
			if _, present := raw[key]; !present {
				return nil, enterpriseProtocol("plan.evidence is missing a required field")
			}
		}
		normalized := map[string]any{}
		if schema, present := raw["schema_version"]; present && schema != nil {
			parsed, schemaErr := enterpriseU64(schema, "plan.evidence.schema_version")
			if schemaErr != nil || parsed != enterpriseSchemaVersion {
				return nil, enterpriseProtocol("plan.evidence.schema_version is unsupported")
			}
			normalized["schema_version"] = enterpriseSchemaVersion
		}
		kind, kindErr := enterpriseEnum(raw["kind"], "plan.evidence.kind", enterpriseEvidenceKinds)
		if kindErr != nil {
			return nil, kindErr
		}
		uri, uriErr := enterpriseIdentifier(raw["uri"], "plan.evidence.uri")
		if uriErr != nil {
			return nil, uriErr
		}
		digest, digestErr := enterpriseIdentifier(raw["digest"], "plan.evidence.digest")
		if digestErr != nil {
			return nil, digestErr
		}
		if schema, present := raw["schema_version"]; present && schema != nil {
			candidate := strings.TrimPrefix(strings.TrimPrefix(digest, "sha256:"), "blake3:")
			if len(candidate) != 64 {
				return nil, enterpriseProtocol("plan.evidence.digest is not a supported versioned digest")
			}
			for _, character := range candidate {
				if !((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f') || (character >= 'A' && character <= 'F')) {
					return nil, enterpriseProtocol("plan.evidence.digest is not a supported versioned digest")
				}
			}
		}
		signature, signatureErr := enterpriseEnum(raw["signature_status"], "plan.evidence.signature_status", enterpriseSignatureStatuses)
		if signatureErr != nil {
			return nil, signatureErr
		}
		normalized["kind"], normalized["uri"], normalized["digest"], normalized["signature_status"] = kind, uri, digest, signature
		if media, present := raw["media_type"]; present && media != nil {
			mediaText, mediaErr := enterpriseIdentifier(media, "plan.evidence.media_type")
			if mediaErr != nil {
				return nil, mediaErr
			}
			normalized["media_type"] = mediaText
		}
		for key, nested := range raw {
			if !enterpriseEvidenceKeys[key] {
				normalized[key] = nested
			}
		}
		result = append(result, normalized)
	}
	return result, nil
}

func enterpriseHeader(value map[string]any, field string) error {
	schema, err := enterpriseU64(value["schema_version"], field+".schema_version")
	if err != nil || schema != enterpriseSchemaVersion {
		return enterpriseProtocol(field + ".schema_version is unsupported")
	}
	transport, err := enterpriseU64(value["transport_version"], field+".transport_version")
	if err != nil || transport != enterpriseTransportVersion {
		return enterpriseProtocol(field + ".transport_version is unsupported")
	}
	version, ok := value["engine_interface_version"].(string)
	if !ok || version != EngineInterfaceVersion {
		return enterpriseProtocol(field + ".engine_interface_version is unsupported")
	}
	return nil
}

func enterpriseSourcePlan(value map[string]any, request EnginePlanningRequest) (map[string]any, error) {
	if err := enterpriseExactKeys(value, map[string]bool{"result": true, "source_bindings": true, "binding_digest": true}, "Engine source-plan response"); err != nil {
		return nil, err
	}
	resultRaw, err := enterpriseObject(value["result"], "Engine source-plan result")
	if err != nil {
		return nil, err
	}
	if err := enterpriseExactKeys(resultRaw, map[string]bool{
		"schema_version": true, "transport_version": true, "engine_interface_version": true, "plan": true,
	}, "Engine context-plan result"); err != nil {
		return nil, err
	}
	if err := enterpriseHeader(resultRaw, "Engine context-plan result"); err != nil {
		return nil, err
	}
	plan, err := enterprisePlan(resultRaw["plan"])
	if err != nil {
		return nil, err
	}
	if _, present := plan["projection_digest"]; !present {
		return nil, enterpriseProtocol("Engine source-plan response requires projection_digest")
	}
	if plan["task_id"] != request.TaskID {
		return nil, enterpriseProtocol("Engine context-plan response task_id does not bind the request")
	}
	if plan["budget_tokens"].(uint64) > request.BudgetTokens {
		return nil, enterpriseProtocol("Engine context-plan response budget exceeds the request")
	}
	result := map[string]any{
		"schema_version": enterpriseSchemaVersion, "transport_version": enterpriseTransportVersion,
		"engine_interface_version": EngineInterfaceVersion, "plan": plan,
	}
	bindingsRaw, ok := value["source_bindings"].([]any)
	if !ok || len(bindingsRaw) > enterpriseMaxPlanItems {
		return nil, enterpriseProtocol("source_bindings has an invalid shape")
	}
	bindings := make([]any, len(bindingsRaw))
	for index, bindingValue := range bindingsRaw {
		binding, bindingErr := enterpriseDescriptor(bindingValue, "source_bindings["+strconv.Itoa(index)+"]")
		if bindingErr != nil {
			return nil, bindingErr
		}
		if index > 0 && bindings[index-1].(map[string]any)["object_ref"].(string) >= binding["object_ref"].(string) {
			return nil, enterpriseProtocol("source_bindings must be strictly sorted by object_ref")
		}
		bindings[index] = binding
	}
	selected := make([]map[string]any, 0)
	selectionValues := plan["selections"].([]any)
	for _, item := range selectionValues {
		selection := item.(map[string]any)
		if selection["disposition"] == "selected" {
			selected = append(selected, selection)
		}
	}
	if len(selected) != len(bindings) {
		return nil, enterpriseProtocol("source_bindings do not match selected plan entries")
	}
	for _, bindingValue := range bindings {
		binding := bindingValue.(map[string]any)
		matched := false
		for _, selection := range selected {
			if selection["source_ref"] == binding["object_ref"] &&
				selection["provider"] == binding["source_id"] &&
				selection["sha256_digest"] == binding["content_digest"] {
				matched = true
				break
			}
		}
		if !matched {
			return nil, enterpriseProtocol("source binding does not match a selected plan entry")
		}
	}
	bindingDigest, err := enterpriseDigest(value["binding_digest"], "binding_digest")
	if err != nil {
		return nil, err
	}
	expectedDigest, digestErr := canonicalDigest([]any{result, bindings})
	if digestErr != nil || bindingDigest != expectedDigest {
		return nil, enterpriseProtocol("binding_digest does not match canonical source bindings")
	}
	return map[string]any{
		"result": result, "source_bindings": bindings, "binding_digest": bindingDigest,
	}, nil
}

func enterpriseSourceScope(plan map[string]any, sourceIDs []string) error {
	requested := make(map[string]bool, len(sourceIDs))
	for _, sourceID := range sourceIDs {
		requested[sourceID] = true
	}
	result := plan["result"].(map[string]any)
	resultPlan := result["plan"].(map[string]any)
	for _, item := range resultPlan["selections"].([]any) {
		selection := item.(map[string]any)
		if !requested[selection["source_ref"].(string)] || !requested[selection["provider"].(string)] {
			return enterpriseProtocol("Enterprise Engine selection is outside requested sources")
		}
	}
	for _, item := range plan["source_bindings"].([]any) {
		binding := item.(map[string]any)
		if !requested[binding["object_ref"].(string)] || !requested[binding["source_id"].(string)] {
			return enterpriseProtocol("Enterprise Engine source binding is outside requested sources")
		}
		if binding["permission"] != "permitted" {
			return enterpriseProtocol("Enterprise Engine selected source is not permitted")
		}
	}
	return nil
}
