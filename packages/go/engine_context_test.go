// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

package leanctx

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"
)

type contextReadCapture struct {
	calls         int
	method        string
	path          string
	authorization string
	contentType   string
	body          []byte
}

func guardedContextFixture(t *testing.T) map[string]any {
	t.Helper()
	path := filepath.Join(testRepositoryRoot(t), "fixtures", "guarded-context-read-v1", "conformance.json")
	payload, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	value, err := strictJSONLoads(payload, filepath.Base(path))
	if err != nil {
		t.Fatal(err)
	}
	return value.(map[string]any)
}

func guardedContextResponse(t *testing.T, overrides map[string]any) ([]byte, map[string]any) {
	t.Helper()
	fixture := guardedContextFixture(t)
	response := fixture["response"].(map[string]any)
	result := response["result"].(map[string]any)
	metadata := result["_meta"].(map[string]any)
	receipt := metadata["canonical_receipt"].(map[string]any)
	for key, value := range overrides {
		receipt[key] = value
	}
	payload, err := json.Marshal(response)
	if err != nil {
		t.Fatal(err)
	}
	return payload, response
}

func newContextReadServer(t *testing.T, status int, payload []byte, delay time.Duration) (*httptest.Server, *contextReadCapture) {
	t.Helper()
	capture := &contextReadCapture{}
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		capture.calls++
		capture.method = request.Method
		capture.path = request.URL.Path
		capture.authorization = request.Header.Get("Authorization")
		capture.contentType = request.Header.Get("Content-Type")
		capture.body, _ = io.ReadAll(request.Body)
		if delay > 0 {
			time.Sleep(delay)
		}
		if status >= 300 && status < 400 {
			response.Header().Set("Location", "http://127.0.0.1:1/v1/tools/call")
		}
		if payload != nil {
			response.Header().Set("Content-Type", "application/json")
		}
		response.WriteHeader(status)
		if payload != nil {
			_, _ = response.Write(payload)
		}
	}))
	t.Cleanup(server.Close)
	return server, capture
}

func newLoopbackContextClient(t *testing.T, server *httptest.Server, timeout time.Duration) *EngineContextClient {
	t.Helper()
	client, err := NewEngineContextClient(
		server.URL,
		"credential-test",
		EngineContextClientOptions{Timeout: timeout, AllowLoopbackHTTP: true},
	)
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func TestEngineContextReadFixtureRoundTrip(t *testing.T) {
	fixture := guardedContextFixture(t)
	payload, responseFixture := guardedContextResponse(t, nil)
	server, capture := newContextReadServer(t, http.StatusOK, payload, 0)
	client := newLoopbackContextClient(t, server, 30*time.Second)
	result, err := client.ContextRead("src/context.rs")
	if err != nil {
		t.Fatal(err)
	}
	if result.Text != "pub fn retained_context() {}" {
		t.Fatalf("unexpected context text: %q", result.Text)
	}
	if !reflect.DeepEqual(result.RawResponse, responseFixture) {
		t.Fatalf("raw response changed: %#v", result.RawResponse)
	}
	if result.CanonicalReceipt["receipt_ref"] != "id:sha256:"+strings.Repeat("a", 64) {
		t.Fatalf("unexpected canonical receipt: %#v", result.CanonicalReceipt)
	}
	requestValue, err := strictJSONLoads(capture.body, "captured request")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(requestValue, fixture["request"]) {
		t.Fatalf("request differs from fixture: %#v", requestValue)
	}
	if capture.calls != 1 || capture.method != http.MethodPost || capture.path != contextReadEndpoint {
		t.Fatalf("unexpected request route: %#v", capture)
	}
	if capture.authorization != "Bearer credential-test" || capture.contentType != "application/json" {
		t.Fatalf("unexpected request authentication or media type: %#v", capture)
	}
	if result.CanonicalReceipt["extension"] != nil || result.RawResponse["result"].(map[string]any)["extension"] == nil {
		t.Fatal("unknown response metadata was not preserved without widening receipt schema")
	}
}

func TestEngineContextReadRejectsFixtureReceiptOverrides(t *testing.T) {
	fixture := guardedContextFixture(t)
	overrides, ok := fixture["invalid_receipt_overrides"].([]any)
	if !ok {
		t.Fatal("fixture invalid_receipt_overrides is not an array")
	}
	for index, rawOverride := range overrides {
		override, ok := rawOverride.(map[string]any)
		if !ok {
			t.Fatalf("fixture override %d is not an object", index)
		}
		t.Run(strconv.Itoa(index), func(t *testing.T) {
			payload, _ := guardedContextResponse(t, override)
			server, _ := newContextReadServer(t, http.StatusOK, payload, 0)
			client := newLoopbackContextClient(t, server, 30*time.Second)
			_, err := client.ContextRead("src/context.rs")
			var protocol *EngineProtocolError
			if !errors.As(err, &protocol) {
				t.Fatalf("receipt override accepted with error %T: %v", err, err)
			}
		})
	}
}

func TestEngineContextReadPreservesUnknownFieldsAndEnforcesLimits(t *testing.T) {
	payload, responseFixture := guardedContextResponse(t, nil)
	server, _ := newContextReadServer(t, http.StatusOK, payload, 0)
	client := newLoopbackContextClient(t, server, 30*time.Second)
	result, err := client.ContextRead("src/context.rs")
	if err != nil {
		t.Fatal(err)
	}
	resultValue := result.RawResponse["result"].(map[string]any)
	fixtureResult := responseFixture["result"].(map[string]any)
	if resultValue["extension"] == nil || fixtureResult["extension"] == nil {
		t.Fatal("unknown result field was lost")
	}
	content := resultValue["content"].([]any)[0].(map[string]any)
	if content["extension"] == nil {
		t.Fatal("unknown content field was lost")
	}
	metadata := resultValue["_meta"].(map[string]any)
	if metadata["extension"] == nil {
		t.Fatal("unknown metadata field was lost")
	}

	oversizedServer := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Length", strconv.Itoa(maxResponseBytes+1))
		response.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(oversizedServer.Close)
	oversizedClient := newLoopbackContextClient(t, oversizedServer, 30*time.Second)
	_, err = oversizedClient.ContextRead("src/context.rs")
	var protocol *EngineProtocolError
	if !errors.As(err, &protocol) {
		t.Fatalf("oversized response error = %T: %v", err, err)
	}

	_, oversizedResponse := guardedContextResponse(t, nil)
	oversizedResult := oversizedResponse["result"].(map[string]any)
	oversizedContent := oversizedResult["content"].([]any)[0].(map[string]any)
	oversizedContent["text"] = strings.Repeat("x", maxTextBytes+1)
	oversizedPayload, err := canonicalJSON(oversizedResponse)
	if err != nil {
		t.Fatal(err)
	}
	textServer, _ := newContextReadServer(t, http.StatusOK, oversizedPayload, 0)
	_, err = newLoopbackContextClient(t, textServer, 30*time.Second).ContextRead("src/context.rs")
	protocol = nil
	if !errors.As(err, &protocol) {
		t.Fatalf("oversized text error = %T: %v", err, err)
	}
}

func TestEngineContextReadRejectsUnsafeConfigurationAndPath(t *testing.T) {
	invalidURLs := []struct {
		value string
		allow bool
	}{
		{"http://127.0.0.1:1234", false},
		{"http://localhost:1234", true},
		{"https://user:secret@example.test", false},
		{"https://example.test/?query=1", false},
		{"https://example.test/#fragment", false},
		{"https://example.test/#", false},
		{"https://example.test/ ", false},
		{"https://example.test/api", false},
		{"https://:443", false},
	}
	for _, test := range invalidURLs {
		_, err := NewEngineContextClient(test.value, "credential-test", EngineContextClientOptions{AllowLoopbackHTTP: test.allow})
		var configuration *ConfigurationError
		if !errors.As(err, &configuration) {
			t.Fatalf("URL %q accepted with error %T: %v", test.value, err, err)
		}
		if strings.Contains(err.Error(), "secret") {
			t.Fatalf("configuration error echoed URL credentials: %v", err)
		}
	}
	for _, credential := range []string{"", "credential\n", strings.Repeat("x", contextReadMaxCredential+1)} {
		_, err := NewEngineContextClient("https://example.test", credential)
		var configuration *ConfigurationError
		if !errors.As(err, &configuration) {
			t.Fatalf("credential accepted with error %T: %v", err, err)
		}
	}
	for _, timeout := range []time.Duration{99 * time.Millisecond, 121 * time.Second} {
		_, err := NewEngineContextClient("https://example.test", "credential-test", EngineContextClientOptions{Timeout: timeout})
		var configuration *ConfigurationError
		if !errors.As(err, &configuration) {
			t.Fatalf("timeout accepted with error %T: %v", err, err)
		}
	}

	server, capture := newContextReadServer(t, http.StatusInternalServerError, nil, 0)
	client := newLoopbackContextClient(t, server, 30*time.Second)
	for _, path := range []string{"", "\x00", string(rune(0x7f)), string(rune(0x80)), string([]byte{0xed, 0xa0, 0x80}), strings.Repeat("x", maxPathBytes+1)} {
		_, err := client.ContextRead(path)
		var validation *ValidationError
		if !errors.As(err, &validation) {
			t.Fatalf("path %q accepted with error %T: %v", path, err, err)
		}
	}
	if capture.calls != 0 {
		t.Fatalf("invalid paths reached the server: %d", capture.calls)
	}
	transport, ok := client.httpClient.Transport.(*http.Transport)
	if !ok || transport.Proxy != nil || !transport.DisableKeepAlives || !transport.DisableCompression || client.httpClient.CheckRedirect == nil {
		t.Fatal("context-read transport is not dedicated, non-proxy, non-reusing, and non-redirecting")
	}
}

func TestEngineContextReadMapsHTTPFailuresAndDeadline(t *testing.T) {
	payload, _ := guardedContextResponse(t, nil)
	tests := []struct {
		name   string
		status int
		check  func(error) bool
	}{
		{"redirect", http.StatusFound, func(err error) bool { var typed *EngineProtocolError; return errors.As(err, &typed) }},
		{"unauthorized", http.StatusUnauthorized, func(err error) bool { var typed *EngineRejected; return errors.As(err, &typed) }},
		{"forbidden", http.StatusForbidden, func(err error) bool { var typed *PolicyAdmissionError; return errors.As(err, &typed) }},
		{"other-client-error", http.StatusNotFound, func(err error) bool { var typed *EngineRejected; return errors.As(err, &typed) }},
		{"server-error", http.StatusBadGateway, func(err error) bool { var typed *EngineUnavailable; return errors.As(err, &typed) }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, capture := newContextReadServer(t, test.status, []byte("credential-test must not appear in errors"), 0)
			client := newLoopbackContextClient(t, server, 30*time.Second)
			_, err := client.ContextRead("src/context.rs")
			if !test.check(err) {
				t.Fatalf("status %d error = %T: %v", test.status, err, err)
			}
			if strings.Contains(err.Error(), "credential-test") || capture.calls != 1 {
				t.Fatalf("unsafe status handling: error=%v capture=%#v", err, capture)
			}
		})
	}

	server, _ := newContextReadServer(t, http.StatusOK, payload, 200*time.Millisecond)
	_, err := newLoopbackContextClient(t, server, 100*time.Millisecond).ContextRead("src/context.rs")
	var timeout *EngineTimeout
	if !errors.As(err, &timeout) {
		t.Fatalf("deadline error = %T: %v", err, err)
	}
}

func TestEngineContextReadUsesFixtureInvalidJSONAsProtocolError(t *testing.T) {
	server, _ := newContextReadServer(t, http.StatusOK, []byte(`{"result": {"content": []}`), 0)
	_, err := newLoopbackContextClient(t, server, 30*time.Second).ContextRead("src/context.rs")
	var protocol *EngineProtocolError
	if !errors.As(err, &protocol) {
		t.Fatalf("malformed response error = %T: %v", err, err)
	}
}

func TestEngineContextReadRejectsUnpairedSurrogates(t *testing.T) {
	payload, _ := guardedContextResponse(t, nil)
	for _, escaped := range []string{`\ud800`, `\udfff`, `\ud800\u0061`} {
		invalid := strings.Replace(string(payload), "pub fn retained_context() {}", escaped, 1)
		_, err := parseEngineContextReadResponse([]byte(invalid))
		var protocol *EngineProtocolError
		if !errors.As(err, &protocol) {
			t.Fatalf("unpaired surrogate accepted: %T", err)
		}
	}
	for _, escaped := range []string{`\ud83d\ude00`, `\\ud800`} {
		valid := strings.Replace(string(payload), "pub fn retained_context() {}", escaped, 1)
		if _, err := parseEngineContextReadResponse([]byte(valid)); err != nil {
			t.Fatalf("valid Unicode or escaped literal rejected: %v", err)
		}
	}
}

func TestEngineContextReadUninitializedClient(t *testing.T) {
	for _, client := range []*EngineContextClient{nil, {}} {
		_, err := client.ContextRead("src/context.rs")
		var configuration *ConfigurationError
		if !errors.As(err, &configuration) {
			t.Fatalf("uninitialized client error = %T", err)
		}
	}
}
