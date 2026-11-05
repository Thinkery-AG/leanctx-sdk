// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
)

const (
	// InstancePresenterCapabilityMinInstanceVersion is the first Engine release
	// that serves the v2 materialization contract.
	InstancePresenterCapabilityMinInstanceVersion = "3.11.0"
	// MaxInstancePresenterTTLSeconds is the longest lifetime a host may ask for.
	MaxInstancePresenterTTLSeconds = 24 * 60 * 60
	// PresenterCapabilityHeader carries a capability to the proxy or the
	// Enterprise suite. It is a request header only, never a body member.
	PresenterCapabilityHeader = "x-leanctx-presenter"

	enterpriseContextMaterializeV2Path = "/v1/engine/context-materialize-v2"
	enterprisePresenterEnvelopeBytes   = 512
	presenterCapabilityPrefix          = "lcp1_"
	presenterReferencePrefix           = "presenter:"
	presenterRedacted                  = "PresenterCapability(<redacted>)"
)

// PresenterCapability is a bearer secret the Engine returns once to the host
// that presented sources. Whoever later shows it is treated as THE HOLDER OF
// THAT CAPABILITY for those presentations. It is not an agent name, not an
// attested identity and not an authorization: current source rights are
// checked again at every use.
//
// It is host-internal: never put it in model context, a tool argument, a tool
// result, a log or a receipt, and never send it to a provider. Every fmt
// rendering and every JSON or text encoding of this type is redacted or
// refused; Reveal is the one deliberate way to the secret.
type PresenterCapability struct {
	secret string
}

// NewPresenterCapability wraps a capability a host kept in its own secret store.
func NewPresenterCapability(secret string) (*PresenterCapability, error) {
	if !lowerHexWithPrefix(secret, presenterCapabilityPrefix, 64) {
		return nil, NewValidationError("presenter capability is malformed")
	}
	return &PresenterCapability{secret: secret}, nil
}

// Reveal returns the secret. Never log, print or forward the result.
func (p *PresenterCapability) Reveal() string {
	if p == nil {
		return ""
	}
	return p.secret
}

// String implements fmt.Stringer with a redacted form.
func (p *PresenterCapability) String() string { return presenterRedacted }

// GoString implements fmt.GoStringer with a redacted form.
func (p *PresenterCapability) GoString() string { return presenterRedacted }

// Format redacts every fmt verb, including %+v and %#v of an enclosing value.
func (p *PresenterCapability) Format(state fmt.State, _ rune) {
	_, _ = io.WriteString(state, presenterRedacted)
}

// MarshalJSON refuses: a capability is never serialized by accident.
func (p *PresenterCapability) MarshalJSON() ([]byte, error) {
	return nil, NewValidationError("a presenter capability is not serializable")
}

// MarshalText refuses for the same reason.
func (p *PresenterCapability) MarshalText() ([]byte, error) {
	return nil, NewValidationError("a presenter capability is not serializable")
}

var (
	_ json.Marshaler = (*PresenterCapability)(nil)
	_ fmt.Formatter  = (*PresenterCapability)(nil)
)

func lowerHexWithPrefix(text, prefix string, digits int) bool {
	hex, found := strings.CutPrefix(text, prefix)
	if !found || len(hex) != digits {
		return false
	}
	for _, character := range []byte(hex) {
		if (character < '0' || character > '9') && (character < 'a' || character > 'f') {
			return false
		}
	}
	return true
}

// InstanceSourceMaterializationRequestV2 is the v1 request plus the presenter
// mode. Set exactly one of PresenterTTLSeconds (issue a new capability with
// this lifetime) and Presenter (renew under a capability this host holds).
type InstanceSourceMaterializationRequestV2 struct {
	InstanceSourceMaterializationRequest
	PresenterTTLSeconds uint32
	Presenter           *PresenterCapability
}

// InstancePresenterGrant names the presenter entry. Capability is set only when
// this call issued one; PresenterRef is content-free and safe in records.
type InstancePresenterGrant struct {
	PresenterRef  string
	ExpiresAtUnix uint64
	Capability    *PresenterCapability
}

// InstanceSourceMaterializationResponseV2 is a validated, tenant-bound v2
// materialization. Materialization is the unchanged v1 exchange.
type InstanceSourceMaterializationResponseV2 struct {
	SchemaVersion      uint64
	TenantID           string
	GovernanceRevision uint64
	Materialization    map[string]any
	Presenter          InstancePresenterGrant
}

// ContextMaterializeSourcesV2 materializes under a presenter capability.
func (c *EnterpriseInstanceClient) ContextMaterializeSourcesV2(request InstanceSourceMaterializationRequestV2) (*InstanceSourceMaterializationResponseV2, error) {
	return c.ContextMaterializeSourcesV2Context(context.Background(), request)
}

// ContextMaterializeSourcesV2Context is the cancellable form. A held
// capability travels in the x-leanctx-presenter request header; a newly issued
// one is returned once, in Presenter.Capability. The capability proves the
// holder of the capability and nothing else, and is usable only with the API
// key it was issued to. An UnsupportedInstanceError means the suite or its
// Engine does not serve the v2 contract.
func (c *EnterpriseInstanceClient) ContextMaterializeSourcesV2Context(parent context.Context, request InstanceSourceMaterializationRequestV2) (*InstanceSourceMaterializationResponseV2, error) {
	if c == nil || c.transport == nil {
		return nil, NewConfigurationError("EnterpriseInstanceClient is not initialized")
	}
	planning, sourceIDs, bindingDigest, err := request.checked()
	if err != nil {
		return nil, err
	}
	held := request.Presenter
	var presenter map[string]any
	switch {
	case held != nil && request.PresenterTTLSeconds == 0 && lowerHexWithPrefix(held.secret, presenterCapabilityPrefix, 64):
		presenter = map[string]any{"mode": "renew"}
	case held == nil && request.PresenterTTLSeconds >= 1 && request.PresenterTTLSeconds <= MaxInstancePresenterTTLSeconds:
		presenter = map[string]any{"mode": "issue", "ttl_seconds": uint64(request.PresenterTTLSeconds)}
	default:
		return nil, NewValidationError("set exactly one of Presenter and a PresenterTTLSeconds of 1 to 86400")
	}
	payloadValue := map[string]any{
		"planning":                     planning.ToDict(),
		"source_ids":                   sourceIDs,
		"expected_governance_revision": request.ExpectedGovernanceRevision,
		"expected_binding_digest":      bindingDigest,
		"presenter":                    presenter,
	}
	if request.PlanningEvaluationTime != nil {
		payloadValue["planning_evaluation_time"] = *request.PlanningEvaluationTime
	}
	payload, err := canonicalJSON(payloadValue)
	if err != nil || len(payload) > enterpriseMaxRequestBytes {
		return nil, NewValidationError("Enterprise instance materialization request exceeds its byte bound")
	}
	raw, err := c.transport.postJSONPresentedContext(
		parent,
		enterpriseContextMaterializeV2Path,
		payload,
		enterpriseMaxMaterializationResponseSize+enterprisePresenterEnvelopeBytes,
		"Enterprise instance materialization",
		"materialization request",
		held,
		"Enterprise suite does not serve context-materialize-v2: instance "+InstancePresenterCapabilityMinInstanceVersion+" or later is required",
	)
	if err != nil {
		return nil, err
	}
	if !contextReadUnicodeEscapesValid(raw) {
		return nil, enterpriseProtocol("Enterprise instance materialization response has an unpaired Unicode surrogate")
	}
	decoded, err := strictJSONLoads(raw, "Enterprise instance materialization response")
	if err != nil {
		return nil, NewInstanceProtocolError("Enterprise instance materialization response is not valid JSON")
	}
	const mismatch = "Enterprise instance materialization response fields do not match the v2 contract"
	wrapper, isObject := decoded.(map[string]any)
	if !isObject || len(wrapper) != 4 {
		return nil, enterpriseProtocol(mismatch)
	}
	engine, isObject := wrapper["materialization"].(map[string]any)
	if !isObject || len(engine) != 3 {
		return nil, enterpriseProtocol(mismatch)
	}
	for _, envelope := range []map[string]any{wrapper, engine} {
		version, versionErr := enterpriseU64(envelope["schema_version"], "response.schema_version")
		if versionErr != nil || version != 2 {
			return nil, enterpriseProtocol(mismatch)
		}
	}
	grantValue, isObject := engine["presenter"].(map[string]any)
	if !isObject {
		return nil, enterpriseProtocol(mismatch)
	}
	// The inner exchange is the unchanged v1 one: validate it as such. Its
	// exact-key check also rejects a v2 wrapper with other members.
	v1, err := c.parseMaterializationWrapper(map[string]any{
		"schema_version":      json.Number("1"),
		"tenant_id":           wrapper["tenant_id"],
		"governance_revision": wrapper["governance_revision"],
		"materialization":     engine["materialization"],
	}, planning, sourceIDs, request.ExpectedGovernanceRevision, bindingDigest)
	if err != nil {
		return nil, err
	}
	content, _ := v1.Materialization["content"].(string)
	grant, err := parsePresenterGrant(grantValue, content, held == nil)
	if err != nil {
		return nil, err
	}
	return &InstanceSourceMaterializationResponseV2{
		SchemaVersion: 2, TenantID: v1.TenantID, GovernanceRevision: v1.GovernanceRevision,
		Materialization: v1.Materialization, Presenter: grant,
	}, nil
}

// parsePresenterGrant validates the grant. issued says whether this call asked
// for a new capability: then exactly one is present, otherwise none.
func parsePresenterGrant(value map[string]any, content string, issued bool) (InstancePresenterGrant, error) {
	for key := range value {
		if key != "presenter_ref" && key != "expires_at_unix" && key != "capability" {
			return InstancePresenterGrant{}, enterpriseProtocol("presenter grant fields do not match the v2 contract")
		}
	}
	reference, isString := value["presenter_ref"].(string)
	if !isString || !lowerHexWithPrefix(reference, presenterReferencePrefix, 32) {
		return InstancePresenterGrant{}, enterpriseProtocol("presenter_ref is malformed")
	}
	expiresAt, err := enterpriseU64(value["expires_at_unix"], "presenter.expires_at_unix")
	if err != nil || expiresAt == 0 {
		return InstancePresenterGrant{}, enterpriseProtocol("presenter expires_at_unix is malformed")
	}
	secretValue, present := value["capability"]
	if present != issued {
		return InstancePresenterGrant{}, enterpriseProtocol("presenter capability presence does not match the request")
	}
	grant := InstancePresenterGrant{PresenterRef: reference, ExpiresAtUnix: expiresAt}
	if issued {
		secret, _ := secretValue.(string)
		if !lowerHexWithPrefix(secret, presenterCapabilityPrefix, 64) {
			return InstancePresenterGrant{}, enterpriseProtocol("presenter capability is malformed")
		}
		if strings.Contains(content, secret) {
			return InstancePresenterGrant{}, enterpriseProtocol("presenter capability appears in materialized content")
		}
		grant.Capability = &PresenterCapability{secret: secret}
	}
	return grant, nil
}
