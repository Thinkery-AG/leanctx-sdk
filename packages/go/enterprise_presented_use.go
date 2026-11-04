// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"context"
	"strings"
	"unicode/utf8"
)

const (
	enterprisePresentedUsePath     = "/v1/engine/presented-use"
	enterprisePresenterRevokePath  = "/v1/engine/presenter-revoke"
	enterprisePresenterUnsupported = "Enterprise suite does not serve presenter capabilities: instance " + InstancePresenterCapabilityMinInstanceVersion + " or later is required"
	// MaxPresentedContentBytes bounds a delivered text sent with a use.
	MaxPresentedContentBytes = 1024 * 1024
)

// PresentedUseMode says what the holder of a capability asks about text that
// was delivered under a presented lineage.
type PresentedUseMode string

const (
	// PresentedUseCheck asks whether the exact delivered text may still be
	// used by this holder now.
	PresentedUseCheck PresentedUseMode = "check"
	// PresentedUseRetain also keeps a recovery copy and returns its id.
	PresentedUseRetain PresentedUseMode = "retain"
	// PresentedUseExpand returns the text of a kept recovery copy.
	PresentedUseExpand PresentedUseMode = "expand"
)

// PresentedUseRequest is one use. Value is the delivered text for check and
// retain, and a recovery id for expand. SourceIDs are the governed sources the
// text was materialized from: exactly those, or the use is refused.
type PresentedUseRequest struct {
	Presenter                  *PresenterCapability
	SourceIDs                  []string
	ExpectedGovernanceRevision uint64
	Mode                       PresentedUseMode
	Value                      string
}

// PresentedUseResponse is the validated, tenant-bound answer. When Usable is
// false, Reason is a content-free code and every other member is empty.
type PresentedUseResponse struct {
	TenantID           string
	GovernanceRevision uint64
	PresenterRef       string
	Usable             bool
	Reason             string
	SourceIDs          []string
	RecoveryID         string
	Content            string
}

// PresenterRevocation is the validated answer to a revocation.
type PresenterRevocation struct {
	TenantID     string
	PresenterRef string
	Revoked      bool
}

// PresentedUse uses text that was materialized under a capability.
func (c *EnterpriseInstanceClient) PresentedUse(request PresentedUseRequest) (*PresentedUseResponse, error) {
	return c.PresentedUseContext(context.Background(), request)
}

// PresentedUseContext is the cancellable form. The capability travels in the
// x-leanctx-presenter request header, never in the body. The suite checks the
// API key, its tenant and its current rights to every source first and again
// before it answers; the Engine checks the capability and the current policy.
// A refused capability is a PolicyAdmissionError; an UnsupportedInstanceError
// means the suite or its Engine does not serve the operation.
func (c *EnterpriseInstanceClient) PresentedUseContext(parent context.Context, request PresentedUseRequest) (*PresentedUseResponse, error) {
	if c == nil || c.transport == nil {
		return nil, NewConfigurationError("EnterpriseInstanceClient is not initialized")
	}
	held := request.Presenter
	if held == nil || !lowerHexWithPrefix(held.secret, presenterCapabilityPrefix, 64) {
		return nil, NewValidationError("a presented use requires a PresenterCapability")
	}
	sourceIDs, err := enterpriseSourceIDs(request.SourceIDs)
	if err != nil {
		return nil, err
	}
	if len(sourceIDs) == 0 {
		return nil, NewValidationError("a presented use requires at least one source id")
	}
	asked := map[string]any{"mode": string(request.Mode)}
	switch request.Mode {
	case PresentedUseCheck, PresentedUseRetain:
		if request.Value == "" || len(request.Value) > MaxPresentedContentBytes || !utf8.ValidString(request.Value) {
			return nil, NewValidationError("presented content is empty, not UTF-8 or exceeds its byte bound")
		}
		asked["content"] = request.Value
	case PresentedUseExpand:
		if !lowerHexWithPrefix(request.Value, "", 64) {
			return nil, NewValidationError("recovery id is malformed")
		}
		asked["recovery_id"] = request.Value
	default:
		return nil, NewValidationError("presented use mode must be check, retain or expand")
	}
	payload, err := canonicalJSON(map[string]any{
		"schema_version":               uint64(1),
		"source_ids":                   sourceIDs,
		"expected_governance_revision": request.ExpectedGovernanceRevision,
		"use":                          asked,
	})
	if err != nil {
		return nil, NewValidationError("presented use request cannot be encoded")
	}
	raw, err := c.transport.postJSONPresentedContext(
		parent,
		enterprisePresentedUsePath,
		payload,
		enterpriseMaxMaterializationResponseSize+enterprisePresenterEnvelopeBytes,
		"Enterprise presented use",
		"presented use request",
		held,
		enterprisePresenterUnsupported,
	)
	if err != nil {
		return nil, err
	}
	const mismatch = "Enterprise presented use response fields do not match the v1 contract"
	wrapper, err := c.presenterEnvelope(raw, "use", 4)
	if err != nil {
		return nil, err
	}
	revision, err := enterpriseU64(wrapper["governance_revision"], "response.governance_revision")
	if err != nil || revision != request.ExpectedGovernanceRevision {
		return nil, enterpriseProtocol("Enterprise presented use governance revision does not match")
	}
	answer := wrapper["use"].(map[string]any)
	for key := range answer {
		switch key {
		case "schema_version", "presenter_ref", "usable", "reason", "source_ids", "recovery_id", "content":
		default:
			return nil, enterpriseProtocol(mismatch)
		}
	}
	version, versionErr := enterpriseU64(answer["schema_version"], "use.schema_version")
	reference, isReference := answer["presenter_ref"].(string)
	usable, isBool := answer["usable"].(bool)
	listed, isList := answer["source_ids"].([]any)
	if versionErr != nil || version != 1 || !isReference || !isBool || !isList ||
		!lowerHexWithPrefix(reference, presenterReferencePrefix, 32) {
		return nil, enterpriseProtocol(mismatch)
	}
	reason, hasReason := answer["reason"].(string)
	recoveryID, hasRecoveryID := answer["recovery_id"].(string)
	content, hasContent := answer["content"].(string)
	_, reasonPresent := answer["reason"]
	_, recoveryPresent := answer["recovery_id"]
	_, contentPresent := answer["content"]
	if reasonPresent != hasReason || recoveryPresent != hasRecoveryID || contentPresent != hasContent {
		return nil, enterpriseProtocol(mismatch)
	}
	response := &PresentedUseResponse{TenantID: c.tenantID, GovernanceRevision: revision, PresenterRef: reference, Usable: usable}
	if !usable {
		if !presentedUseReason(reason) || len(listed) != 0 || recoveryPresent || contentPresent {
			return nil, enterpriseProtocol(mismatch)
		}
		response.Reason = reason
		return response, nil
	}
	expanding := request.Mode == PresentedUseExpand
	if reasonPresent || len(listed) != len(sourceIDs) ||
		recoveryPresent != (request.Mode != PresentedUseCheck) || contentPresent != expanding ||
		(recoveryPresent && !lowerHexWithPrefix(recoveryID, "", 64)) ||
		(expanding && recoveryID != request.Value) ||
		len(content) > MaxPresentedContentBytes || strings.Contains(content, held.secret) {
		return nil, enterpriseProtocol(mismatch)
	}
	requested := make(map[string]bool, len(sourceIDs))
	for _, id := range sourceIDs {
		requested[id] = true
	}
	for _, item := range listed {
		id, isString := item.(string)
		if !isString || !requested[id] {
			return nil, enterpriseProtocol("Enterprise presented use is outside requested sources")
		}
		delete(requested, id)
		response.SourceIDs = append(response.SourceIDs, id)
	}
	response.RecoveryID, response.Content = recoveryID, content
	return response, nil
}

// RevokePresenter ends a capability: from now on it presents and reuses nothing.
func (c *EnterpriseInstanceClient) RevokePresenter(presenter *PresenterCapability) (*PresenterRevocation, error) {
	return c.RevokePresenterContext(context.Background(), presenter)
}

// RevokePresenterContext is the cancellable form. Only the API key a
// capability was issued to can revoke it.
func (c *EnterpriseInstanceClient) RevokePresenterContext(parent context.Context, presenter *PresenterCapability) (*PresenterRevocation, error) {
	if c == nil || c.transport == nil {
		return nil, NewConfigurationError("EnterpriseInstanceClient is not initialized")
	}
	if presenter == nil || !lowerHexWithPrefix(presenter.secret, presenterCapabilityPrefix, 64) {
		return nil, NewValidationError("a revocation requires a PresenterCapability")
	}
	raw, err := c.transport.postJSONPresentedContext(
		parent,
		enterprisePresenterRevokePath,
		[]byte(`{"schema_version":1}`),
		enterprisePresenterEnvelopeBytes,
		"Enterprise presenter revocation",
		"presenter revocation request",
		presenter,
		enterprisePresenterUnsupported,
	)
	if err != nil {
		return nil, err
	}
	const mismatch = "Enterprise presenter revocation fields do not match the v1 contract"
	wrapper, err := c.presenterEnvelope(raw, "revocation", 3)
	if err != nil {
		return nil, err
	}
	revocation := wrapper["revocation"].(map[string]any)
	version, versionErr := enterpriseU64(revocation["schema_version"], "revocation.schema_version")
	reference, isReference := revocation["presenter_ref"].(string)
	revoked, isBool := revocation["revoked"].(bool)
	if len(revocation) != 3 || versionErr != nil || version != 1 || !isReference || !isBool ||
		!lowerHexWithPrefix(reference, presenterReferencePrefix, 32) {
		return nil, enterpriseProtocol(mismatch)
	}
	return &PresenterRevocation{TenantID: c.tenantID, PresenterRef: reference, Revoked: revoked}, nil
}

// presenterEnvelope decodes the suite envelope of a presenter operation: an
// object of exactly `members` members with schema_version 1, this client's
// tenant and an object under `member`.
func (c *EnterpriseInstanceClient) presenterEnvelope(raw []byte, member string, members int) (map[string]any, error) {
	const mismatch = "Enterprise presenter response fields do not match the contract"
	if !contextReadUnicodeEscapesValid(raw) {
		return nil, enterpriseProtocol("Enterprise presenter response has an unpaired Unicode surrogate")
	}
	decoded, err := strictJSONLoads(raw, "Enterprise presenter response")
	if err != nil {
		return nil, NewInstanceProtocolError("Enterprise presenter response is not valid JSON")
	}
	wrapper, isObject := decoded.(map[string]any)
	if !isObject || len(wrapper) != members {
		return nil, enterpriseProtocol(mismatch)
	}
	if _, isObject = wrapper[member].(map[string]any); !isObject {
		return nil, enterpriseProtocol(mismatch)
	}
	version, err := enterpriseU64(wrapper["schema_version"], "response.schema_version")
	if err != nil || version != 1 {
		return nil, enterpriseProtocol(mismatch)
	}
	tenantID, err := enterpriseUUIDValue(wrapper["tenant_id"], "response.tenant_id")
	if err != nil || tenantID != c.tenantID {
		return nil, enterpriseProtocol("Enterprise presenter response tenant binding does not match")
	}
	return wrapper, nil
}

// presentedUseReason accepts the Engine's fixed, content-free refusal codes.
func presentedUseReason(reason string) bool {
	code, found := strings.CutPrefix(reason, "lineage.")
	if !found {
		code, found = strings.CutPrefix(reason, "recovery.")
	}
	if !found || code == "" || len(code) > 48 {
		return false
	}
	for _, character := range []byte(code) {
		if (character < 'a' || character > 'z') && character != '_' {
			return false
		}
	}
	return true
}
