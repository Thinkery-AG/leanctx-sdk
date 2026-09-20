// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package leanctx

import (
	"encoding/json"
	"testing"
)

func TestCanonicalStringsPreserveLiteralEscapesAndUnicode(t *testing.T) {
	cases := []struct{ input, expected string }{
		{`<>&`, `"<>&"`},
		{`\u003c\u003e\u0026`, `"\\u003c\\u003e\\u0026"`},
		{`\\u003c`, `"\\\\u003c"`},
		{"\u2028\u2029😀", "\"\u2028\u2029😀\""},
		{`\u2028`, `"\\u2028"`},
		{"\x00\x1f\b\f\n\r\t", `"\u0000\u001f\b\f\n\r\t"`},
		{"\"\\/", `"\"\\/"`},
	}
	for _, tc := range cases {
		encoded, err := canonicalJSON(tc.input)
		if err != nil || string(encoded) != tc.expected {
			t.Fatalf("canonical string %q: got %s, want %s, error %v", tc.input, encoded, tc.expected, err)
		}
		var roundTrip string
		if err := json.Unmarshal(encoded, &roundTrip); err != nil || roundTrip != tc.input {
			t.Fatalf("canonical string lost its original bytes: %q", tc.input)
		}
	}
	encoded, err := canonicalJSON(map[string]any{`\u003c`: "\u2028"})
	if err != nil || string(encoded) != "{\"\\\\u003c\":\"\u2028\"}" {
		t.Fatalf("canonical map key escaped incorrectly: %s, %v", encoded, err)
	}
}
