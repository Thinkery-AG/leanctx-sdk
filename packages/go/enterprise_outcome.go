// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"bytes"
	"context"
	"strings"
)

// EngineOutcomeRequest carries operator signals, not a caller signing identity.
type EngineOutcomeRequest struct {
	TaskID                string
	ReceiptDigest         string
	ContextDecisionDigest string
	Signals               []map[string]any
}

// EngineOutcomeResponse preserves the host's attestation projection. Selected
// document joins are checked, but signer admission and evaluation remain remote.
type EngineOutcomeResponse struct {
	SchemaVersion         uint64
	TenantID              string
	ReceiptID             string
	ReceiptDigest         string
	OriginalReceiptDigest string
	Acceptance            string
	AlreadyRecorded       bool
	ReceiptDocumentJSON   string
	ReceiptDocumentBytes  []byte
	RawResponse           map[string]any
}

// ContextOutcome sends one authenticated attestation; it never retries a POST.
func (c *EnterpriseEngineClient) ContextOutcome(request EngineOutcomeRequest) (*EngineOutcomeResponse, error) {
	return c.ContextOutcomeContext(context.Background(), request)
}

// ContextOutcomeContext is the cancellable form of ContextOutcome.
func (c *EnterpriseEngineClient) ContextOutcomeContext(parent context.Context, request EngineOutcomeRequest) (*EngineOutcomeResponse, error) {
	if c == nil || c.transport == nil {
		return nil, NewConfigurationError("EnterpriseEngineClient is not initialized")
	}
	payload, err := enterpriseOutcomeInput(request)
	if err != nil {
		return nil, err
	}
	raw, err := c.transport.postJSONContext(parent, "/v1/engine/context-outcome", payload,
		enterpriseMaxExecutionTotalBytes, "Enterprise outcome", "outcome request")
	if err != nil {
		return nil, err
	}
	return parseEnterpriseOutcome(raw, request, c.tenantID)
}

func enterpriseOutcomeInput(request EngineOutcomeRequest) ([]byte, error) {
	if _, err := enterpriseExecutionText(request.TaskID, "task_id", 256, false, false, false); err != nil {
		return nil, err
	}
	if err := validateDigest(request.ReceiptDigest, "receipt_digest"); err != nil {
		return nil, err
	}
	if err := validateDigest(request.ContextDecisionDigest, "context_decision_digest"); err != nil {
		return nil, err
	}
	if len(request.Signals) == 0 || len(request.Signals) > 16 {
		return nil, NewValidationError("outcome signals require between 1 and 16 entries")
	}
	for _, signal := range request.Signals {
		fields := map[string]bool{"signal_type": true, "value": true}
		if err := enterpriseExecutionFields(signal, fields, fields, "outcome signal", false); err != nil {
			return nil, err
		}
		switch signal["signal_type"] {
		case "build_success", "tests_passing", "lint_clean", "typecheck_passing", "human_acceptance", "pr_merge", "ci_passing", "correction", "rollback":
		default:
			return nil, NewValidationError("unsupported outcome signal type")
		}
		if unknown, ok := signal["value"].(string); ok && unknown == "unknown" {
			continue
		}
		value, ok := signal["value"].(map[string]any)
		if !ok || len(value) != 1 {
			return nil, NewValidationError("outcome signal value must be unknown, boolean or count")
		}
		if boolean, present := value["boolean"]; present {
			if _, ok := boolean.(bool); !ok {
				return nil, NewValidationError("outcome signal boolean must be a boolean")
			}
		} else if count, present := value["count"]; present {
			if _, err := enterpriseExecutionBoundedU64(count, "outcome signal count", enterpriseMaxExecutionU32, false); err != nil {
				return nil, err
			}
		} else {
			return nil, NewValidationError("unsupported outcome signal value")
		}
	}
	payload, err := canonicalJSON(map[string]any{
		"task_id": request.TaskID, "receipt_digest": request.ReceiptDigest,
		"context_decision_digest": request.ContextDecisionDigest, "signals": request.Signals,
	})
	if err != nil || len(payload) > enterpriseMaxExecutionRequestBytes {
		return nil, NewValidationError("outcome request exceeds its canonical byte bound")
	}
	return payload, nil
}

func enterpriseOutcomeObject(value any, fields []string, label string) (map[string]any, error) {
	object, err := enterpriseExecutionObject(value, label, true)
	if err != nil {
		return nil, err
	}
	exact := make(map[string]bool, len(fields))
	for _, field := range fields {
		exact[field] = true
	}
	if err := enterpriseExecutionFields(object, exact, exact, label, true); err != nil {
		return nil, err
	}
	if err := enterpriseExecutionVersion(object["schema_version"], label+".schema_version", true); err != nil {
		return nil, err
	}
	return object, nil
}

func parseEnterpriseOutcome(raw []byte, request EngineOutcomeRequest, tenantID string) (*EngineOutcomeResponse, error) {
	if len(raw) > enterpriseMaxExecutionTotalBytes || !contextReadUnicodeEscapesValid(raw) {
		return nil, enterpriseProtocol("outcome response exceeds bounds or contains invalid Unicode")
	}
	decoded, err := strictJSONLoads(raw, "Enterprise outcome response")
	if err != nil {
		return nil, enterpriseProtocol("outcome response is not valid JSON")
	}
	response, err := enterpriseOutcomeObject(decoded, []string{"schema_version", "tenant_id", "outcome"}, "outcome response")
	if err != nil {
		return nil, err
	}
	if response["tenant_id"] != tenantID {
		return nil, enterpriseProtocol("outcome tenant binding differs")
	}
	outcome, err := enterpriseOutcomeObject(response["outcome"], []string{
		"schema_version", "receipt_id", "receipt_digest", "original_receipt_digest",
		"acceptance", "already_recorded", "receipt_document_json",
	}, "outcome")
	if err != nil {
		return nil, err
	}
	receiptID, err := enterpriseDigest(outcome["receipt_id"], "outcome.receipt_id")
	if err != nil {
		return nil, err
	}
	digest, err := enterpriseDigest(outcome["receipt_digest"], "outcome.receipt_digest")
	if err != nil {
		return nil, err
	}
	if outcome["original_receipt_digest"] != request.ReceiptDigest {
		return nil, enterpriseProtocol("outcome original receipt differs")
	}
	acceptance, ok := outcome["acceptance"].(string)
	if !ok || (acceptance != "accepted" && acceptance != "rejected") {
		return nil, enterpriseProtocol("outcome acceptance is unsupported")
	}
	recorded, ok := outcome["already_recorded"].(bool)
	if !ok {
		return nil, enterpriseProtocol("outcome already_recorded must be a boolean")
	}
	document, err := enterpriseExecutionText(outcome["receipt_document_json"], "outcome document", enterpriseMaxExecutionReceiptBytes, true, false, false)
	if err != nil {
		return nil, err
	}
	documentBytes := []byte(document)
	if sha256Hex(documentBytes) != digest {
		return nil, enterpriseProtocol("outcome receipt document digest differs")
	}
	if err := enterpriseOutcomeDocument(documentBytes, receiptID, acceptance, request); err != nil {
		return nil, err
	}
	return &EngineOutcomeResponse{
		SchemaVersion: 1, TenantID: tenantID, ReceiptID: receiptID, ReceiptDigest: digest,
		OriginalReceiptDigest: request.ReceiptDigest, Acceptance: acceptance, AlreadyRecorded: recorded,
		ReceiptDocumentJSON: document, ReceiptDocumentBytes: documentBytes, RawResponse: response,
	}, nil
}

func enterpriseOutcomeDocument(raw []byte, receiptID, acceptance string, request EngineOutcomeRequest) error {
	if !contextReadUnicodeEscapesValid(raw) {
		return enterpriseProtocol("outcome document contains invalid Unicode")
	}
	decoded, err := strictJSONLoads(raw, "outcome receipt document")
	if err != nil {
		return enterpriseProtocol("outcome receipt document is not valid JSON")
	}
	document, err := enterpriseOutcomeObject(decoded, []string{
		"schema_version", "receipt_id", "lineage", "chain", "status", "values",
		"outcome", "evidence_refs", "issued_at", "signer", "signature",
	}, "outcome receipt document")
	if err != nil {
		return err
	}
	canonical, err := canonicalJSON(document)
	if err != nil || !bytes.Equal(canonical, raw) {
		return enterpriseProtocol("outcome receipt bytes are not canonical")
	}
	identity := make(map[string]any, len(document)-2)
	for field, value := range document {
		if field != "receipt_id" && field != "signature" {
			identity[field] = value
		}
	}
	identityDigest, err := canonicalDigest(identity)
	if err != nil || identityDigest != receiptID || document["receipt_id"] != receiptID {
		return enterpriseProtocol("outcome receipt identity differs")
	}
	outcome, ok := document["outcome"].(map[string]any)
	if !ok || outcome["state"] != acceptance {
		return enterpriseProtocol("outcome receipt state differs")
	}
	chain, ok := document["chain"].(map[string]any)
	if !ok {
		return enterpriseProtocol("outcome receipt chain is invalid")
	}
	if _, err := enterpriseDigest(chain["previous_receipt_id"], "outcome predecessor"); err != nil {
		return err
	}
	lineage, ok := document["lineage"].(map[string]any)
	if !ok || lineage["task_id"] != request.TaskID {
		return enterpriseProtocol("outcome receipt task differs")
	}
	references, ok := document["evidence_refs"].([]any)
	if !ok {
		return enterpriseProtocol("outcome receipt evidence references are invalid")
	}
	found := false
	for _, reference := range references {
		entry, ok := reference.(map[string]any)
		if !ok || entry["kind"] != "runtime" {
			continue
		}
		found = true
		if entry["digest"] != request.ContextDecisionDigest || entry["uri"] != "artifact://execution/evidence/"+strings.TrimPrefix(request.ContextDecisionDigest, "sha256:") {
			return enterpriseProtocol("outcome runtime evidence differs")
		}
	}
	if !found {
		return enterpriseProtocol("outcome receipt has no runtime evidence")
	}
	return nil
}
