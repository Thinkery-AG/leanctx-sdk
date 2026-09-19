// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const (
	contextReadEndpoint       = "/v1/tools/call"
	contextReadDefaultTimeout = 30 * time.Second
	contextReadMaxCredential  = 4096
	contextReadMaxURL         = 4096
)

// EngineContextClientOptions configures the guarded Engine context-read client.
type EngineContextClientOptions struct {
	Timeout           time.Duration
	AllowLoopbackHTTP bool
}

// EngineContextReadResult contains guarded text and the host's receipt metadata.
// The metadata is not a signed receipt artifact and does not establish tenant binding.
type EngineContextReadResult struct {
	Text             string
	CanonicalReceipt map[string]any
	RawResponse      map[string]any
}

// EngineContextClient calls the public Engine's guarded context-read boundary.
type EngineContextClient struct {
	endpoint   string
	credential string
	timeout    time.Duration
	httpClient *http.Client
}

// NewEngineContextClient constructs a bounded authenticated context-read client.
func NewEngineContextClient(baseURL, credential string, options ...EngineContextClientOptions) (*EngineContextClient, error) {
	if len(options) > 1 {
		return nil, NewConfigurationError("at most one EngineContextClientOptions value is allowed")
	}
	option := EngineContextClientOptions{Timeout: contextReadDefaultTimeout}
	if len(options) == 1 {
		option = options[0]
		if option.Timeout == 0 {
			option.Timeout = contextReadDefaultTimeout
		}
	}
	if option.Timeout < 100*time.Millisecond || option.Timeout > 120*time.Second {
		return nil, NewConfigurationError("timeout must be between 0.1 and 120 seconds")
	}
	endpoint, err := contextReadEndpointURL(baseURL, option.AllowLoopbackHTTP)
	if err != nil {
		return nil, err
	}
	checkedCredential, err := contextReadCredential(credential)
	if err != nil {
		return nil, err
	}
	transport := &http.Transport{
		Proxy:              nil,
		DisableKeepAlives:  true,
		DisableCompression: true,
	}
	return &EngineContextClient{
		endpoint:   endpoint,
		credential: checkedCredential,
		timeout:    option.Timeout,
		httpClient: &http.Client{Transport: transport, CheckRedirect: noContextReadRedirect},
	}, nil
}

func noContextReadRedirect(_ *http.Request, _ []*http.Request) error {
	return http.ErrUseLastResponse
}

// ContextRead executes one guarded Engine read with the client's bounded deadline.
func (c *EngineContextClient) ContextRead(path string) (*EngineContextReadResult, error) {
	return c.ContextReadContext(context.Background(), path)
}

// ContextReadContext executes one guarded Engine read with caller cancellation.
func (c *EngineContextClient) ContextReadContext(parent context.Context, path string) (*EngineContextReadResult, error) {
	if c == nil || c.httpClient == nil {
		return nil, NewConfigurationError("EngineContextClient is not initialized")
	}
	if parent == nil {
		parent = context.Background()
	}
	checkedPath, err := contextReadPath(path)
	if err != nil {
		return nil, err
	}
	payload, err := canonicalJSON(map[string]any{
		"arguments": map[string]any{
			"engine_interface": "v1",
			"mode":             "aggressive",
			"path":             checkedPath,
		},
		"name": "ctx_read",
	})
	if err != nil {
		return nil, NewEngineProtocolError("Engine context-read request is not canonical JSON")
	}
	if len(payload) > maxRequestBytes {
		return nil, NewEngineProtocolError("Engine context-read request exceeds its byte bound")
	}
	requestContext, cancel := context.WithTimeout(parent, c.timeout)
	defer cancel()
	request, err := http.NewRequestWithContext(requestContext, http.MethodPost, c.endpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, NewEngineUnavailable("Engine context-read request could not be created")
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+c.credential)
	request.Header.Set("Connection", "close")
	response, err := c.httpClient.Do(request)
	if err != nil {
		return nil, contextReadTransportError(requestContext, err)
	}
	defer response.Body.Close()
	if err := contextReadStatus(response.StatusCode); err != nil {
		return nil, err
	}
	if response.ContentLength > int64(maxResponseBytes) {
		return nil, NewEngineProtocolError("Engine context-read response exceeds its byte bound")
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, int64(maxResponseBytes)+1))
	if err != nil {
		return nil, contextReadTransportError(requestContext, err)
	}
	if len(raw) > maxResponseBytes {
		return nil, NewEngineProtocolError("Engine context-read response exceeds its byte bound")
	}
	return parseEngineContextReadResponse(raw)
}

func contextReadTransportError(ctx context.Context, err error) error {
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return NewEngineTimeout("Engine context-read request exceeded its deadline")
	}
	return NewEngineUnavailable("Engine context-read request failed")
}

func contextReadStatus(status int) error {
	switch {
	case status >= 300 && status < 400:
		return NewEngineProtocolError("Engine context-read redirects are not followed")
	case status == http.StatusUnauthorized:
		return NewEngineRejected("Engine context-read authentication was rejected", nil, nil)
	case status == http.StatusForbidden:
		return NewPolicyAdmissionError("Engine context-read policy rejected the request", nil, nil)
	case status >= 500:
		return NewEngineUnavailable("Engine context-read returned a server error")
	case status < 200 || status >= 300:
		return NewEngineRejected("Engine context-read rejected the request", nil, nil)
	default:
		return nil
	}
}

func contextReadEndpointURL(raw string, allowLoopbackHTTP bool) (string, error) {
	if raw == "" || !utf8.ValidString(raw) || len([]byte(raw)) > contextReadMaxURL {
		return "", NewConfigurationError("base_url must be a bounded absolute URL")
	}
	for _, character := range raw {
		if unicode.IsControl(character) || unicode.IsSpace(character) {
			return "", NewConfigurationError("base_url must not contain whitespace or controls")
		}
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return "", NewConfigurationError("base_url is not a valid URL")
	}
	if parsed.User != nil {
		return "", NewConfigurationError("base_url must not contain userinfo")
	}
	if parsed.Hostname() == "" || parsed.Opaque != "" {
		return "", NewConfigurationError("base_url must contain a host")
	}
	if strings.ContainsAny(raw, "?#") || parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" || parsed.Path != "" && parsed.Path != "/" {
		return "", NewConfigurationError("base_url may contain only an optional root path")
	}
	scheme := strings.ToLower(parsed.Scheme)
	if scheme != "https" && scheme != "http" {
		return "", NewConfigurationError("base_url must use HTTPS")
	}
	if portText := parsed.Port(); portText != "" {
		port, parseErr := strconv.Atoi(portText)
		if parseErr != nil || port < 1 || port > 65535 {
			return "", NewConfigurationError("base_url port is outside its bounds")
		}
	}
	if scheme == "http" {
		if !allowLoopbackHTTP {
			return "", NewConfigurationError("HTTP is only allowed for explicit loopback testing")
		}
		address := net.ParseIP(parsed.Hostname())
		if address == nil || !address.IsLoopback() {
			return "", NewConfigurationError("loopback HTTP requires a literal loopback IP")
		}
	}
	parsed.Scheme = scheme
	parsed.Path = contextReadEndpoint
	parsed.RawPath = ""
	parsed.RawQuery = ""
	parsed.ForceQuery = false
	parsed.Fragment = ""
	return parsed.String(), nil
}

func contextReadCredential(value string) (string, error) {
	if !utf8.ValidString(value) || len([]byte(value)) == 0 || len([]byte(value)) > contextReadMaxCredential {
		return "", NewConfigurationError("credential must be a non-empty visible ASCII string")
	}
	for _, character := range value {
		if character < 0x21 || character > 0x7e {
			return "", NewConfigurationError("credential must be a non-empty visible ASCII string")
		}
	}
	return value, nil
}

func contextReadPath(value string) (string, error) {
	if !utf8.ValidString(value) || len([]byte(value)) == 0 || len([]byte(value)) > maxPathBytes {
		return "", NewValidationError("context-read path exceeds its byte bound")
	}
	for _, character := range value {
		if unicode.IsControl(character) {
			return "", NewValidationError("context-read path contains a control character")
		}
	}
	return value, nil
}

func parseEngineContextReadResponse(raw []byte) (*EngineContextReadResult, error) {
	if !contextReadUnicodeEscapesValid(raw) {
		return nil, NewEngineProtocolError("Engine context-read response has an unpaired Unicode surrogate")
	}
	decoded, err := strictJSONLoads(raw, "Engine context-read response")
	if err != nil {
		return nil, NewEngineProtocolError("Engine context-read response is not valid JSON")
	}
	wrapper, err := objectValue(decoded, "Engine context-read response")
	if err != nil || len(wrapper) != 1 {
		return nil, NewEngineProtocolError("Engine context-read response wrapper is invalid")
	}
	resultValue, ok := wrapper["result"]
	if !ok {
		return nil, NewEngineProtocolError("Engine context-read response wrapper is invalid")
	}
	result, err := objectValue(resultValue, "Engine context-read result")
	if err != nil {
		return nil, err
	}
	if isError, present := result["isError"]; present && isError != nil {
		value, ok := isError.(bool)
		if !ok || value {
			return nil, NewEngineProtocolError("Engine context-read returned an error result")
		}
	}
	contentValue, ok := result["content"]
	if !ok {
		return nil, NewEngineProtocolError("Engine context-read content must contain one item")
	}
	content, ok := contentValue.([]any)
	if !ok || len(content) != 1 {
		return nil, NewEngineProtocolError("Engine context-read content must contain one item")
	}
	contentItem, err := objectValue(content[0], "Engine context-read content")
	if err != nil {
		return nil, err
	}
	contentType, ok := contentItem["type"].(string)
	if !ok || contentType != "text" {
		return nil, NewEngineProtocolError("Engine context-read content must be text")
	}
	text, err := contextReadText(contentItem["text"])
	if err != nil {
		return nil, err
	}
	metadataValue, ok := result["_meta"]
	if !ok {
		return nil, NewEngineProtocolError("Engine context-read metadata is missing")
	}
	metadata, err := objectValue(metadataValue, "Engine context-read metadata")
	if err != nil {
		return nil, err
	}
	receiptValue, ok := metadata["canonical_receipt"]
	if !ok {
		return nil, NewEngineProtocolError("Engine context-read metadata is missing")
	}
	receipt, err := parseContextReceipt(receiptValue)
	if err != nil {
		return nil, err
	}
	return &EngineContextReadResult{Text: text, CanonicalReceipt: receipt, RawResponse: wrapper}, nil
}

func contextReadText(value any) (string, error) {
	text, ok := value.(string)
	if !ok || !utf8.ValidString(text) || len([]byte(text)) > maxTextBytes {
		return "", NewEngineProtocolError("Engine context-read response text exceeds its byte bound")
	}
	return text, nil
}

func parseContextReceipt(value any) (map[string]any, error) {
	receipt, err := objectValue(value, "Engine context-read canonical receipt")
	if err != nil {
		return nil, err
	}
	allowed := map[string]bool{
		"schema_version": true, "receipt_id": true, "receipt_ref": true,
		"receipt_digest": true, "outcome": true, "delivery": true,
		"context_decision_ref": true, "outcome_observation_ref": true,
	}
	required := map[string]bool{
		"schema_version": true, "receipt_id": true, "receipt_ref": true,
		"receipt_digest": true, "outcome": true, "delivery": true,
	}
	if err := allowedObjectKeys(receipt, allowed, required, "Engine context-read canonical receipt"); err != nil {
		return nil, err
	}
	schema, ok := receipt["schema_version"].(json.Number)
	if !ok || schema.String() != "1" {
		return nil, NewEngineProtocolError("Engine context-read canonical receipt schema_version is unsupported")
	}
	if _, err := protocolRef(receipt["receipt_id"], "canonical_receipt.receipt_id"); err != nil {
		return nil, err
	}
	digest, err := requiredDigest(receipt["receipt_digest"], "canonical_receipt.receipt_digest")
	if err != nil {
		return nil, err
	}
	reference, err := protocolRef(receipt["receipt_ref"], "canonical_receipt.receipt_ref")
	if err != nil {
		return nil, err
	}
	if reference != "id:"+digest {
		return nil, NewEngineProtocolError("Engine context-read canonical receipt reference is not digest-bound")
	}
	outcome, ok := receipt["outcome"].(string)
	if !ok || outcome != "unknown" {
		return nil, NewEngineProtocolError("Engine context-read canonical receipt outcome is unsupported")
	}
	delivery, ok := receipt["delivery"].(string)
	if !ok || delivery != "native_engine_view" {
		return nil, NewEngineProtocolError("Engine context-read canonical receipt delivery is unsupported")
	}
	for _, field := range []string{"context_decision_ref", "outcome_observation_ref"} {
		if value, present := receipt[field]; present {
			if _, err := protocolRef(value, "canonical_receipt."+field); err != nil {
				return nil, err
			}
		}
	}
	return receipt, nil
}

// encoding/json replaces unpaired surrogate escapes with U+FFFD. Reject them
// before decoding so receipt-bearing source text is never silently rewritten.
func contextReadUnicodeEscapesValid(raw []byte) bool {
	for index := 0; index < len(raw); index++ {
		if raw[index] != '\\' {
			continue
		}
		index++
		if index >= len(raw) || raw[index] != 'u' {
			continue
		}
		if index+4 >= len(raw) {
			return false
		}
		value, err := strconv.ParseUint(string(raw[index+1:index+5]), 16, 16)
		if err != nil {
			return false
		}
		index += 4
		if value >= 0xdc00 && value <= 0xdfff {
			return false
		}
		if value < 0xd800 || value > 0xdbff {
			continue
		}
		if index+6 >= len(raw) || raw[index+1] != '\\' || raw[index+2] != 'u' {
			return false
		}
		low, err := strconv.ParseUint(string(raw[index+3:index+7]), 16, 16)
		if err != nil || low < 0xdc00 || low > 0xdfff {
			return false
		}
		index += 6
	}
	return true
}
