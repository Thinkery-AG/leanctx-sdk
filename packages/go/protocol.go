package leanctx

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	maxRequestBytes  = 64 * 1024
	maxPathBytes     = 4096
	maxRefBytes      = 512
	maxTaskBytes     = 16 * 1024
	maxTextBytes     = 8 * 1024 * 1024
	maxResponseBytes = 16 * 1024 * 1024
	maxStderrBytes   = 64 * 1024
	maxRefs          = 32
	maxMeasurements  = 32
	maxSafeInteger   = int64(9007199254740991)
)

var (
	digestPattern     = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	outputPattern     = regexp.MustCompile(`^output:[0-9a-f]{64}$`)
	planPattern       = regexp.MustCompile(`^plan:sha256:[0-9a-f]{64}$`)
	semverPattern     = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)
	namePattern       = regexp.MustCompile(`^[a-z0-9_]+$`)
	jsonNumberPattern = regexp.MustCompile(`^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$`)
)

// FailureCode is the Engine's stable failure classification.
type FailureCode string

const (
	FailureCodePolicyRejected          FailureCode = "policy_rejected"
	FailureCodeSourceUnavailable       FailureCode = "source_unavailable"
	FailureCodeSourceIntegrityMismatch FailureCode = "source_integrity_mismatch"
	FailureCodeResourceLimit           FailureCode = "resource_limit"
	FailureCodeUnsupportedOperation    FailureCode = "unsupported_operation"
	FailureCodeInternal                FailureCode = "internal"
)

func (c FailureCode) valid() bool {
	switch c {
	case FailureCodePolicyRejected, FailureCodeSourceUnavailable,
		FailureCodeSourceIntegrityMismatch, FailureCodeResourceLimit,
		FailureCodeUnsupportedOperation, FailureCodeInternal:
		return true
	default:
		return false
	}
}

// SessionState is the Product lifecycle state.
type SessionState string

const (
	SessionStateCreated   SessionState = "created"
	SessionStatePlanned   SessionState = "planned"
	SessionStateExecuting SessionState = "executing"
	SessionStateCompleted SessionState = "completed"
	SessionStateAborted   SessionState = "aborted"
	SessionStateClosed    SessionState = "closed"
)

// EngineStatus is the status recorded by Engine observation evidence.
type EngineStatus string

const (
	EngineStatusSucceeded EngineStatus = "succeeded"
	EngineStatusDegraded  EngineStatus = "degraded"
	EngineStatusRejected  EngineStatus = "rejected"
	EngineStatusFailed    EngineStatus = "failed"
)

// HostOutcome records the host-owned agent result.
type HostOutcome string

const (
	HostOutcomeUnknown   HostOutcome = "unknown"
	HostOutcomeAccepted  HostOutcome = "accepted"
	HostOutcomeRejected  HostOutcome = "rejected"
	HostOutcomeCompleted HostOutcome = "completed"
	HostOutcomeFailed    HostOutcome = "failed"
	HostOutcomeAborted   HostOutcome = "aborted"
)

// Integrity describes whether Engine evidence is complete and bound.
type Integrity string

const (
	IntegritySealed   Integrity = "sealed"
	IntegrityUnsealed Integrity = "unsealed"
)

// Freshness controls Engine cache reuse.
type Freshness string

const (
	FreshnessReuse   Freshness = "reuse"
	FreshnessRefresh Freshness = "refresh"
)

func validStatus(status EngineStatus) bool {
	return status == EngineStatusSucceeded || status == EngineStatusDegraded ||
		status == EngineStatusRejected || status == EngineStatusFailed
}

func validOutcome(outcome HostOutcome) bool {
	return outcome == HostOutcomeUnknown || outcome == HostOutcomeAccepted ||
		outcome == HostOutcomeRejected || outcome == HostOutcomeCompleted ||
		outcome == HostOutcomeFailed || outcome == HostOutcomeAborted
}

func utf8String(value, field string) error {
	if !utf8.ValidString(value) {
		return NewValidationError(field + " is not valid UTF-8")
	}
	return nil
}

func boundedText(value, field string, maximum int, controls bool) error {
	if err := utf8String(value, field); err != nil {
		return err
	}
	if value == "" {
		return NewValidationError(field + " must not be empty")
	}
	if len([]byte(value)) > maximum {
		return NewValidationError(fmt.Sprintf("%s exceeds %d UTF-8 bytes", field, maximum))
	}
	if strings.IndexByte(value, 0) >= 0 {
		return NewValidationError(field + " contains NUL")
	}
	if controls {
		for _, r := range value {
			if r < 0x20 {
				return NewValidationError(field + " contains a control character")
			}
		}
	}
	return nil
}

func validateRef(value, field string) error {
	if err := utf8String(value, field); err != nil {
		return err
	}
	if len([]byte(value)) == 0 || len([]byte(value)) > maxRefBytes {
		return NewValidationError(fmt.Sprintf("%s must be 1..%d printable ASCII bytes", field, maxRefBytes))
	}
	for _, r := range value {
		if r < 0x20 || r > 0x7e {
			return NewValidationError(fmt.Sprintf("%s must be 1..%d printable ASCII bytes", field, maxRefBytes))
		}
	}
	return nil
}

func validateDigest(value, field string) error {
	if err := validateRef(value, field); err != nil {
		return err
	}
	if !digestPattern.MatchString(value) {
		return NewValidationError(field + " must be sha256:<64 lowercase hex>")
	}
	return nil
}

func validateOutputRef(value, field string) error {
	if err := validateRef(value, field); err != nil {
		return err
	}
	if !outputPattern.MatchString(value) {
		return NewValidationError(field + " must be output:<64 lowercase hex>")
	}
	return nil
}

// canonicalJSON is the compact UTF-8 JSON form used by Product hashes.
// It intentionally accepts only the portable safe-integer JSON domain.
func canonicalJSON(value any) ([]byte, error) {
	plain, err := plainValue(value, make(map[visit]bool))
	if err != nil {
		return nil, NewValidationError("value is not canonical JSON data")
	}
	var out bytes.Buffer
	if err := encodeCanonical(&out, plain); err != nil {
		return nil, NewValidationError("value is not canonical JSON data")
	}
	return out.Bytes(), nil
}

func canonicalDigest(value any) (string, error) {
	data, err := canonicalJSON(value)
	if err != nil {
		return "", err
	}
	return sha256Hex(data), nil
}

type visit struct {
	kind reflect.Kind
	ptr  uintptr
}

func plainValue(value any, seen map[visit]bool) (any, error) {
	switch item := value.(type) {
	case nil, bool, string:
		if text, ok := item.(string); ok && !utf8.ValidString(text) {
			return nil, fmt.Errorf("invalid UTF-8")
		}
		return item, nil
	case json.Number:
		return normalizeNumber(item.String())
	case ContextSource:
		return plainMap(item.ToDict())
	case *ContextSource:
		if item == nil {
			return nil, nil
		}
		return plainMap(item.ToDict())
	case ContextPlan:
		return plainMap(item.ToDict())
	case *ContextPlan:
		if item == nil {
			return nil, nil
		}
		return plainMap(item.ToDict())
	case ContextMeasurement:
		return plainMap(item.ToDict())
	case *ContextMeasurement:
		if item == nil {
			return nil, nil
		}
		return plainMap(item.ToDict())
	case ContextFailure:
		return plainMap(item.ToDict())
	case *ContextFailure:
		if item == nil {
			return nil, nil
		}
		return plainMap(item.ToDict())
	case ContextReceiptLink:
		return plainMap(item.ToDict())
	case *ContextReceiptLink:
		if item == nil {
			return nil, nil
		}
		return plainMap(item.ToDict())
	case RecoveredSource:
		return plainMap(item.ToDict())
	case *RecoveredSource:
		if item == nil {
			return nil, nil
		}
		return plainMap(item.ToDict())
	case ContextView:
		return plainMap(item.ToDict())
	case *ContextView:
		if item == nil {
			return nil, nil
		}
		return plainMap(item.ToDict())
	case map[string]any:
		return plainMapWithSeen(item, seen)
	case []any:
		return plainSliceWithSeen(item, seen)
	}

	rv := reflect.ValueOf(value)
	if !rv.IsValid() {
		return nil, nil
	}
	if rv.Kind() == reflect.Interface {
		return plainValue(rv.Elem().Interface(), seen)
	}
	switch rv.Kind() {
	case reflect.String:
		text := rv.String()
		if !utf8.ValidString(text) {
			return nil, fmt.Errorf("invalid UTF-8")
		}
		return text, nil
	case reflect.Bool:
		return rv.Bool(), nil
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return rv.Int(), nil
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return rv.Uint(), nil
	case reflect.Float32, reflect.Float64:
		return rv.Float(), nil
	case reflect.Pointer:
		if rv.IsNil() {
			return nil, nil
		}
		return plainValue(rv.Elem().Interface(), seen)
	case reflect.Map:
		if rv.Type().Key().Kind() != reflect.String || rv.IsNil() {
			if rv.IsNil() {
				return nil, nil
			}
			return nil, fmt.Errorf("map keys must be strings")
		}
		key := visit{kind: rv.Kind(), ptr: rv.Pointer()}
		if seen[key] {
			return nil, fmt.Errorf("cyclic value")
		}
		seen[key] = true
		defer delete(seen, key)
		result := make(map[string]any, rv.Len())
		iter := rv.MapRange()
		for iter.Next() {
			plain, err := plainValue(iter.Value().Interface(), seen)
			if err != nil {
				return nil, err
			}
			result[iter.Key().String()] = plain
		}
		return result, nil
	case reflect.Array, reflect.Slice:
		if rv.Kind() == reflect.Slice && rv.IsNil() {
			return nil, nil
		}
		key := visit{kind: rv.Kind(), ptr: rv.Pointer()}
		if key.ptr != 0 && seen[key] {
			return nil, fmt.Errorf("cyclic value")
		}
		if key.ptr != 0 {
			seen[key] = true
			defer delete(seen, key)
		}
		result := make([]any, rv.Len())
		for i := 0; i < rv.Len(); i++ {
			plain, err := plainValue(rv.Index(i).Interface(), seen)
			if err != nil {
				return nil, err
			}
			result[i] = plain
		}
		return result, nil
	default:
		return nil, fmt.Errorf("unsupported JSON value %T", value)
	}
}

func plainMap(value map[string]any) (any, error) {
	return plainMapWithSeen(value, make(map[visit]bool))
}

func plainMapWithSeen(value map[string]any, seen map[visit]bool) (any, error) {
	key := visit{kind: reflect.Map, ptr: reflect.ValueOf(value).Pointer()}
	if key.ptr != 0 && seen[key] {
		return nil, fmt.Errorf("cyclic value")
	}
	if key.ptr != 0 {
		seen[key] = true
		defer delete(seen, key)
	}
	result := make(map[string]any, len(value))
	for k, item := range value {
		plain, err := plainValue(item, seen)
		if err != nil {
			return nil, err
		}
		result[k] = plain
	}
	return result, nil
}

func plainSliceWithSeen(value []any, seen map[visit]bool) (any, error) {
	key := visit{kind: reflect.Slice, ptr: reflect.ValueOf(value).Pointer()}
	if key.ptr != 0 && seen[key] {
		return nil, fmt.Errorf("cyclic value")
	}
	if key.ptr != 0 {
		seen[key] = true
		defer delete(seen, key)
	}
	result := make([]any, len(value))
	for i, item := range value {
		plain, err := plainValue(item, seen)
		if err != nil {
			return nil, err
		}
		result[i] = plain
	}
	return result, nil
}

func encodeCanonical(out *bytes.Buffer, value any) error {
	switch item := value.(type) {
	case nil:
		out.WriteString("null")
	case bool:
		if item {
			out.WriteString("true")
		} else {
			out.WriteString("false")
		}
	case string:
		encoded, err := json.Marshal(item)
		if err != nil {
			return err
		}
		// encoding/json HTML-escapes characters that Python/Node leave alone.
		encoded = bytes.ReplaceAll(encoded, []byte(`\u003c`), []byte("<"))
		encoded = bytes.ReplaceAll(encoded, []byte(`\u003e`), []byte(">"))
		encoded = bytes.ReplaceAll(encoded, []byte(`\u0026`), []byte("&"))
		out.Write(encoded)
	case int64:
		if item < -maxSafeInteger || item > maxSafeInteger {
			return fmt.Errorf("unsafe integer")
		}
		out.WriteString(strconv.FormatInt(item, 10))
	case uint64:
		if item > uint64(maxSafeInteger) {
			return fmt.Errorf("unsafe integer")
		}
		out.WriteString(strconv.FormatUint(item, 10))
	case float64:
		if math.IsNaN(item) || math.IsInf(item, 0) || math.Trunc(item) != item || math.Abs(item) > float64(maxSafeInteger) || (item == 0 && math.Signbit(item)) {
			return fmt.Errorf("unsafe number")
		}
		out.WriteString(strconv.FormatInt(int64(item), 10))
	case []any:
		out.WriteByte('[')
		for i, child := range item {
			if i > 0 {
				out.WriteByte(',')
			}
			if err := encodeCanonical(out, child); err != nil {
				return err
			}
		}
		out.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(item))
		for key := range item {
			if !utf8.ValidString(key) {
				return fmt.Errorf("invalid key")
			}
			keys = append(keys, key)
		}
		sortCodePoints(keys)
		out.WriteByte('{')
		for i, key := range keys {
			if i > 0 {
				out.WriteByte(',')
			}
			if err := encodeCanonical(out, key); err != nil {
				return err
			}
			out.WriteByte(':')
			if err := encodeCanonical(out, item[key]); err != nil {
				return err
			}
		}
		out.WriteByte('}')
	default:
		return fmt.Errorf("unsupported normalized value %T", value)
	}
	return nil
}

func sortCodePoints(keys []string) {
	sort.Slice(keys, func(i, j int) bool {
		left, right := []rune(keys[i]), []rune(keys[j])
		for n := 0; n < len(left) && n < len(right); n++ {
			if left[n] != right[n] {
				return left[n] < right[n]
			}
		}
		return len(left) < len(right)
	})
}

func normalizeNumber(value string) (int64, error) {
	if !jsonNumberPattern.MatchString(value) {
		return 0, fmt.Errorf("empty number")
	}
	negative := strings.HasPrefix(value, "-")
	if negative {
		value = value[1:]
	}
	mantissa, exponentText, hasExponent := value, "0", false
	if index := strings.IndexAny(value, "eE"); index >= 0 {
		mantissa, exponentText, hasExponent = value[:index], value[index+1:], true
	}
	exponent := 0
	if hasExponent {
		parsed, err := strconv.Atoi(exponentText)
		if err != nil || parsed < -100000 || parsed > 100000 {
			return 0, fmt.Errorf("unsafe number")
		}
		exponent = parsed
	}
	integer, fraction := mantissa, ""
	if index := strings.IndexByte(mantissa, '.'); index >= 0 {
		integer, fraction = mantissa[:index], mantissa[index+1:]
	}
	digits := strings.TrimLeft(integer+fraction, "0")
	if digits == "" {
		if negative {
			return 0, fmt.Errorf("negative zero")
		}
		return 0, nil
	}
	scale := len(fraction) - exponent
	if scale > 0 {
		cut := len(digits) - scale
		if cut <= 0 {
			return 0, fmt.Errorf("unsafe number")
		}
		for _, digit := range digits[cut:] {
			if digit != '0' {
				return 0, fmt.Errorf("non-integer number")
			}
		}
		digits = strings.TrimLeft(digits[:cut], "0")
		if digits == "" {
			if negative {
				return 0, fmt.Errorf("negative zero")
			}
			return 0, nil
		}
	} else if -scale > 16-len(digits) {
		return 0, fmt.Errorf("unsafe number")
	} else if scale < 0 {
		digits += strings.Repeat("0", -scale)
	}
	if len(digits) > 16 {
		return 0, fmt.Errorf("unsafe number")
	}
	parsed, err := strconv.ParseInt(digits, 10, 64)
	if err != nil || parsed > maxSafeInteger {
		return 0, fmt.Errorf("unsafe number")
	}
	if negative {
		parsed = -parsed
	}
	return parsed, nil
}

// strictJSONLoads rejects duplicates, malformed UTF-8, trailing input, and
// non-object top-level values while preserving JSON numbers for validation.
func strictJSONLoads(data []byte, label string) (any, error) {
	if !utf8.Valid(data) {
		return nil, NewValidationError("invalid " + label)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	value, err := decodeStrictValue(decoder)
	if err != nil {
		return nil, NewValidationError("invalid " + label)
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, NewValidationError("invalid " + label + ": trailing data")
	}
	if _, ok := value.(map[string]any); !ok {
		return nil, NewValidationError(label + " must be a JSON object")
	}
	return value, nil
}

func decodeStrictValue(decoder *json.Decoder) (any, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	if delimiter, ok := token.(json.Delim); ok {
		switch delimiter {
		case '{':
			result := make(map[string]any)
			for decoder.More() {
				keyToken, err := decoder.Token()
				if err != nil {
					return nil, err
				}
				key, ok := keyToken.(string)
				if !ok {
					return nil, fmt.Errorf("object key is not string")
				}
				if _, exists := result[key]; exists {
					return nil, fmt.Errorf("duplicate key")
				}
				child, err := decodeStrictValue(decoder)
				if err != nil {
					return nil, err
				}
				result[key] = child
			}
			end, err := decoder.Token()
			if err != nil || end != json.Delim('}') {
				return nil, fmt.Errorf("object not closed")
			}
			return result, nil
		case '[':
			result := make([]any, 0)
			for decoder.More() {
				child, err := decodeStrictValue(decoder)
				if err != nil {
					return nil, err
				}
				result = append(result, child)
			}
			end, err := decoder.Token()
			if err != nil || end != json.Delim(']') {
				return nil, fmt.Errorf("array not closed")
			}
			return result, nil
		default:
			return nil, fmt.Errorf("unexpected delimiter")
		}
	}
	return token, nil
}

// ContextSourceOptions configures a source descriptor.
type ContextSourceOptions struct {
	ProjectRoot  string
	MediaType    string
	SourceRef    string
	SourceDigest string
}

// ContextSource identifies one project-relative source and its optional
// content-addressed binding.
type ContextSource struct {
	Path         string
	ProjectRoot  string
	MediaType    string
	SourceRef    string
	SourceDigest string
}

// NewContextSource validates and returns a detached source value.
func NewContextSource(path string, options ...ContextSourceOptions) (*ContextSource, error) {
	if len(options) > 1 {
		return nil, NewValidationError("at most one ContextSourceOptions value is allowed")
	}
	option := ContextSourceOptions{}
	if len(options) == 1 {
		option = options[0]
	}
	if option.ProjectRoot == "" {
		var err error
		option.ProjectRoot, err = os.Getwd()
		if err != nil {
			return nil, NewValidationError("project_root is unavailable")
		}
	}
	if err := boundedText(path, "path", maxPathBytes, true); err != nil {
		return nil, err
	}
	if err := boundedText(option.ProjectRoot, "project_root", maxPathBytes, false); err != nil {
		return nil, err
	}
	root, err := filepath.Abs(filepath.Clean(option.ProjectRoot))
	if err != nil || len([]byte(root)) > maxPathBytes {
		return nil, NewValidationError("project_root exceeds the path bound")
	}
	candidate := path
	if filepath.IsAbs(path) {
		candidate = filepath.Clean(path)
	} else {
		candidate = filepath.Join(root, path)
	}
	candidate, err = filepath.Abs(filepath.Clean(candidate))
	if err != nil {
		return nil, NewValidationError("source path is invalid")
	}
	if !containedPath(candidate, root) {
		return nil, NewValidationError("source path escapes project_root")
	}
	if len([]byte(candidate)) > maxPathBytes {
		return nil, NewValidationError("path exceeds the path bound")
	}
	storedPath := filepath.Clean(path)
	if filepath.IsAbs(path) {
		storedPath = candidate
	}
	mediaType := option.MediaType
	if mediaType == "" {
		mediaType = "text/plain"
	}
	if err := boundedText(mediaType, "media_type", maxRefBytes, true); err != nil {
		return nil, err
	}
	if option.SourceRef != "" {
		if err := validateRef(option.SourceRef, "source_ref"); err != nil {
			return nil, err
		}
	}
	if option.SourceDigest != "" {
		if err := validateDigest(option.SourceDigest, "source_digest"); err != nil {
			return nil, err
		}
	}
	return &ContextSource{Path: filepath.ToSlash(storedPath), ProjectRoot: filepath.ToSlash(root), MediaType: mediaType, SourceRef: option.SourceRef, SourceDigest: option.SourceDigest}, nil
}

func containedPath(candidate, root string) bool {
	rel, err := filepath.Rel(root, candidate)
	if err != nil {
		return false
	}
	return rel == "." || (rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel))
}

func (s ContextSource) validate() error {
	if s.ProjectRoot == "" {
		return NewValidationError("project_root must be a directory")
	}
	copy, err := NewContextSource(s.Path, ContextSourceOptions{ProjectRoot: s.ProjectRoot, MediaType: s.MediaType, SourceRef: s.SourceRef, SourceDigest: s.SourceDigest})
	if err != nil {
		return err
	}
	if copy.Path != filepath.ToSlash(s.Path) && !filepath.IsAbs(s.Path) {
		return NewValidationError("source path is not normalized")
	}
	return nil
}

// RelativePath returns the normalized project-relative source path.
func (s ContextSource) RelativePath() (string, error) {
	if err := s.validate(); err != nil {
		return "", err
	}
	root, err := filepath.Abs(filepath.Clean(s.ProjectRoot))
	if err != nil {
		return "", NewValidationError("project_root is invalid")
	}
	candidate := s.Path
	if !filepath.IsAbs(candidate) {
		candidate = filepath.Join(root, candidate)
	}
	candidate, err = filepath.Abs(filepath.Clean(candidate))
	if err != nil || !containedPath(candidate, root) {
		return "", NewValidationError("source containment cannot be proven")
	}
	rel, err := filepath.Rel(root, candidate)
	if err != nil {
		return "", NewValidationError("source path must be a rooted relative file path")
	}
	rel = filepath.ToSlash(rel)
	if rel == "" || rel == "." || rel == ".." || strings.HasPrefix(rel, "../") || strings.IndexByte(rel, 0) >= 0 {
		return "", NewValidationError("source path must be a rooted relative file path")
	}
	for _, r := range rel {
		if r < 0x20 {
			return "", NewValidationError("source path must be a rooted relative file path")
		}
	}
	return rel, nil
}

// Descriptor is the source projection used in the plan identity.
func (s ContextSource) Descriptor() map[string]any {
	relative, err := s.RelativePath()
	if err != nil {
		return map[string]any{}
	}
	result := map[string]any{"path": relative, "media_type": s.MediaType}
	if s.SourceRef != "" {
		result["source_ref"] = s.SourceRef
	}
	if s.SourceDigest != "" {
		result["source_digest"] = s.SourceDigest
	}
	return result
}

// ToDict returns a detached JSON projection.
func (s ContextSource) ToDict() map[string]any {
	result := s.Descriptor()
	result["project_root"] = s.ProjectRoot
	return result
}

func (s ContextSource) MarshalJSON() ([]byte, error) { return json.Marshal(s.ToDict()) }

// ContextPlanOptions configures a Product plan.
type ContextPlanOptions struct {
	Mode      string
	Freshness Freshness
}

// ContextPlan is the immutable, hash-addressed Product intent.
type ContextPlan struct {
	SessionID string
	TaskID    string
	Task      string
	Source    ContextSource
	Mode      string
	Freshness Freshness
	PlanID    string
}

// NewContextPlan validates a Product intent and computes its plan identity.
func NewContextPlan(sessionID, taskID, task string, source ContextSource, options ...ContextPlanOptions) (*ContextPlan, error) {
	if len(options) > 1 {
		return nil, NewValidationError("at most one ContextPlanOptions value is allowed")
	}
	option := ContextPlanOptions{Mode: "aggressive", Freshness: FreshnessReuse}
	if len(options) == 1 {
		if options[0].Mode != "" {
			option.Mode = options[0].Mode
		}
		if options[0].Freshness != "" {
			option.Freshness = options[0].Freshness
		}
	}
	if err := boundedText(sessionID, "session_id", maxRefBytes, true); err != nil {
		return nil, err
	}
	if err := boundedText(taskID, "task_id", maxRefBytes, true); err != nil {
		return nil, err
	}
	if err := boundedText(task, "task", maxTaskBytes, false); err != nil {
		return nil, err
	}
	if err := source.validate(); err != nil {
		return nil, err
	}
	if option.Mode != "aggressive" {
		return nil, NewValidationError("mode must be aggressive in Engine Interface v1")
	}
	if option.Freshness != FreshnessReuse && option.Freshness != FreshnessRefresh {
		return nil, NewValidationError("freshness must be reuse or refresh")
	}
	plan := &ContextPlan{SessionID: sessionID, TaskID: taskID, Task: task, Source: source, Mode: option.Mode, Freshness: option.Freshness}
	digest, err := canonicalDigest(plan.ToIntent())
	if err != nil {
		return nil, err
	}
	plan.PlanID = strings.Replace(digest, "sha256:", "plan:sha256:", 1)
	return plan, nil
}

func (p ContextPlan) ToIntent() map[string]any {
	return map[string]any{"intent_version": int64(1), "session_id": p.SessionID, "task_id": p.TaskID, "task": p.Task, "source": p.Source.Descriptor(), "mode": p.Mode, "freshness": string(p.Freshness)}
}

func (p ContextPlan) ToDict() map[string]any {
	result := p.ToIntent()
	result["plan_id"] = p.PlanID
	return result
}

func (p ContextPlan) MarshalJSON() ([]byte, error) { return json.Marshal(p.ToDict()) }

// ContextMeasurement is one bounded Engine measurement.
type ContextMeasurement struct {
	Name           string
	Unit           string
	Classification string
	Value          *int64
}

func NewContextMeasurement(name, unit, classification string, value *int64) (*ContextMeasurement, error) {
	if !namePattern.MatchString(name) {
		return nil, NewValidationError("measurement name must be lowercase ASCII")
	}
	if !namePattern.MatchString(unit) {
		return nil, NewValidationError("measurement unit must be lowercase ASCII")
	}
	if classification != "measured" && classification != "estimated" && classification != "unavailable" {
		return nil, NewValidationError("invalid measurement classification")
	}
	if classification == "unavailable" {
		if value != nil {
			return nil, NewValidationError("unavailable measurement value must be null")
		}
	} else if value == nil || *value < 0 || *value > maxSafeInteger {
		return nil, NewValidationError("measurement value must be a non-negative integer")
	}
	var copied *int64
	if value != nil {
		item := *value
		copied = &item
	}
	return &ContextMeasurement{Name: name, Unit: unit, Classification: classification, Value: copied}, nil
}

func (m ContextMeasurement) validate() error {
	_, err := NewContextMeasurement(m.Name, m.Unit, m.Classification, m.Value)
	return err
}

func (m ContextMeasurement) ToDict() map[string]any {
	var value any
	if m.Value != nil {
		value = *m.Value
	}
	return map[string]any{"name": m.Name, "unit": m.Unit, "classification": m.Classification, "value": value}
}

// ContextFailure is typed Engine failure evidence.
type ContextFailure struct {
	Code            FailureCode
	RetryableByHost bool
	RecoveryRef     string
}

func NewContextFailure(code FailureCode, retryableByHost bool, recoveryRef string) (*ContextFailure, error) {
	if !code.valid() {
		return nil, NewValidationError("invalid failure code")
	}
	if recoveryRef != "" {
		if err := validateRef(recoveryRef, "recovery_ref"); err != nil {
			return nil, err
		}
	}
	return &ContextFailure{Code: code, RetryableByHost: retryableByHost, RecoveryRef: recoveryRef}, nil
}

func (f ContextFailure) validate() error {
	_, err := NewContextFailure(f.Code, f.RetryableByHost, f.RecoveryRef)
	return err
}

func (f ContextFailure) ToDict() map[string]any {
	var recovery any
	if f.RecoveryRef != "" {
		recovery = f.RecoveryRef
	}
	return map[string]any{"code": string(f.Code), "retryable_by_host": f.RetryableByHost, "recovery_ref": recovery}
}

// ContextReceiptLink binds a Product receipt to one Engine invocation.
type ContextReceiptLink struct {
	SchemaVersion int
	ReceiptID     string
	ReceiptRef    string
	ReceiptDigest string
	InvocationID  string
}

func NewContextReceiptLink(schemaVersion int, receiptID, receiptRef, receiptDigest, invocationID string) (*ContextReceiptLink, error) {
	if schemaVersion != SchemaVersion {
		return nil, NewValidationError("receipt link schema_version must be 1")
	}
	if err := validateRef(receiptID, "receipt_id"); err != nil {
		return nil, err
	}
	if err := validateRef(receiptRef, "receipt_ref"); err != nil {
		return nil, err
	}
	if err := validateDigest(receiptDigest, "receipt_digest"); err != nil {
		return nil, err
	}
	if err := boundedText(invocationID, "invocation_id", maxRefBytes, true); err != nil {
		return nil, err
	}
	if receiptRef != "receipt:"+receiptDigest {
		return nil, NewValidationError("receipt_ref does not match receipt_digest")
	}
	return &ContextReceiptLink{SchemaVersion: schemaVersion, ReceiptID: receiptID, ReceiptRef: receiptRef, ReceiptDigest: receiptDigest, InvocationID: invocationID}, nil
}

func (l ContextReceiptLink) validate() error {
	_, err := NewContextReceiptLink(l.SchemaVersion, l.ReceiptID, l.ReceiptRef, l.ReceiptDigest, l.InvocationID)
	return err
}

func (l ContextReceiptLink) ToDict() map[string]any {
	return map[string]any{"schema_version": int64(l.SchemaVersion), "receipt_id": l.ReceiptID, "receipt_ref": l.ReceiptRef, "receipt_digest": l.ReceiptDigest, "invocation_id": l.InvocationID}
}

// RecoveredSource is content-addressed source returned by exact recovery.
type RecoveredSource struct {
	Text         string
	SourceRef    string
	SourceDigest string
	RecoveryRef  string
}

func NewRecoveredSource(text, sourceRef, sourceDigest, recoveryRef string) (*RecoveredSource, error) {
	if err := boundedText(text, "recovered text", maxTextBytes, false); err != nil {
		return nil, err
	}
	if err := validateRef(sourceRef, "source_ref"); err != nil {
		return nil, err
	}
	if err := validateDigest(sourceDigest, "source_digest"); err != nil {
		return nil, err
	}
	if err := validateRef(recoveryRef, "recovery_ref"); err != nil {
		return nil, err
	}
	digest := sha256Hex([]byte(text))
	if digest != sourceDigest {
		return nil, NewValidationError("recovered text digest does not match source_digest")
	}
	return &RecoveredSource{Text: text, SourceRef: sourceRef, SourceDigest: sourceDigest, RecoveryRef: recoveryRef}, nil
}

func (s RecoveredSource) ToDict() map[string]any {
	return map[string]any{"text": s.Text, "source_ref": s.SourceRef, "source_digest": s.SourceDigest, "recovery_ref": s.RecoveryRef}
}

// ContextViewOptions configures an Engine view.
type ContextViewOptions struct {
	Source                 ContextSource
	Text                   *string
	OutputRef              *string
	OutputDigest           *string
	SourceRef              string
	SourceDigest           string
	RecoveryRef            string
	Status                 EngineStatus
	Measurements           []ContextMeasurement
	Failure                *ContextFailure
	ReceiptLink            *ContextReceiptLink
	Invocation             map[string]any
	Observation            map[string]any
	SchemaVersion          int
	TransportVersion       int
	EngineInterfaceVersion string
}

// ContextView is the validated Product view and Engine evidence projection.
type ContextView struct {
	Source                 ContextSource
	Text                   *string
	OutputRef              *string
	OutputDigest           *string
	SourceRef              string
	SourceDigest           string
	RecoveryRef            string
	Status                 EngineStatus
	Measurements           []ContextMeasurement
	Failure                *ContextFailure
	ReceiptLink            *ContextReceiptLink
	Invocation             map[string]any
	Observation            map[string]any
	SchemaVersion          int
	TransportVersion       int
	EngineInterfaceVersion string
}

// NewContextView validates and copies an Engine view.
func NewContextView(options ContextViewOptions) (*ContextView, error) {
	if err := options.Source.validate(); err != nil {
		return nil, NewValidationError("view source is invalid")
	}
	if options.Text != nil {
		if err := utf8String(*options.Text, "view text"); err != nil {
			return nil, err
		}
		if len([]byte(*options.Text)) > maxTextBytes {
			return nil, NewValidationError("view text exceeds the bound")
		}
	}
	if options.OutputRef != nil {
		if err := validateOutputRef(*options.OutputRef, "output_ref"); err != nil {
			return nil, err
		}
	}
	if options.OutputDigest != nil {
		if err := validateDigest(*options.OutputDigest, "output_digest"); err != nil {
			return nil, err
		}
	}
	if (options.OutputRef == nil) != (options.OutputDigest == nil) {
		return nil, NewValidationError("output_ref and output_digest must be paired")
	}
	if options.OutputDigest != nil && options.Text != nil {
		if sha256Hex([]byte(*options.Text)) != *options.OutputDigest {
			return nil, NewValidationError("view output digest mismatch")
		}
		if *options.OutputRef != "output:"+strings.TrimPrefix(*options.OutputDigest, "sha256:") {
			return nil, NewValidationError("view output reference mismatch")
		}
	}
	if err := validateRef(options.SourceRef, "source_ref"); err != nil {
		return nil, err
	}
	if err := validateDigest(options.SourceDigest, "source_digest"); err != nil {
		return nil, err
	}
	if options.RecoveryRef != "" {
		if err := validateRef(options.RecoveryRef, "recovery_ref"); err != nil {
			return nil, err
		}
	}
	if !validStatus(options.Status) {
		return nil, NewValidationError("invalid Engine observation status")
	}
	if len(options.Measurements) > maxMeasurements {
		return nil, NewValidationError("too many measurements")
	}
	measurements := append([]ContextMeasurement(nil), options.Measurements...)
	for _, item := range measurements {
		if err := item.validate(); err != nil {
			return nil, err
		}
	}
	if options.Failure != nil {
		if err := options.Failure.validate(); err != nil {
			return nil, err
		}
	}
	if options.ReceiptLink != nil {
		if err := options.ReceiptLink.validate(); err != nil {
			return nil, err
		}
	}
	invocation, err := cloneMap(options.Invocation)
	if err != nil {
		return nil, NewValidationError("invocation must be deterministic JSON data")
	}
	observation, err := cloneMap(options.Observation)
	if err != nil {
		return nil, NewValidationError("observation must be deterministic JSON data")
	}
	schema := options.SchemaVersion
	if schema == 0 {
		schema = SchemaVersion
	}
	transport := options.TransportVersion
	if transport == 0 {
		transport = TransportVersion
	}
	engineVersion := options.EngineInterfaceVersion
	if engineVersion == "" {
		engineVersion = EngineInterfaceVersion
	}
	if schema != SchemaVersion {
		return nil, NewValidationError("view schema_version must be 1")
	}
	if transport != TransportVersion {
		return nil, NewValidationError("view transport_version must be integer 1")
	}
	if engineVersion != EngineInterfaceVersion {
		return nil, NewValidationError("unsupported Engine Interface version")
	}
	copyOptions := options
	copyOptions.Text = cloneStringPointer(options.Text)
	copyOptions.OutputRef = cloneStringPointer(options.OutputRef)
	copyOptions.OutputDigest = cloneStringPointer(options.OutputDigest)
	copyOptions.Measurements = measurements
	copyOptions.Invocation = invocation
	copyOptions.Observation = observation
	copyOptions.SchemaVersion, copyOptions.TransportVersion, copyOptions.EngineInterfaceVersion = schema, transport, engineVersion
	return &ContextView{Source: copyOptions.Source, Text: copyOptions.Text, OutputRef: copyOptions.OutputRef, OutputDigest: copyOptions.OutputDigest, SourceRef: copyOptions.SourceRef, SourceDigest: copyOptions.SourceDigest, RecoveryRef: copyOptions.RecoveryRef, Status: copyOptions.Status, Measurements: copyOptions.Measurements, Failure: copyOptions.Failure, ReceiptLink: copyOptions.ReceiptLink, Invocation: copyOptions.Invocation, Observation: copyOptions.Observation, SchemaVersion: schema, TransportVersion: transport, EngineInterfaceVersion: engineVersion}, nil
}

func cloneStringPointer(value *string) *string {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func cloneMap(value map[string]any) (map[string]any, error) {
	if value == nil {
		return map[string]any{}, nil
	}
	plain, err := plainValue(value, make(map[visit]bool))
	if err != nil {
		return nil, err
	}
	result, ok := plain.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("not object")
	}
	return result, nil
}

func (v ContextView) IntegrityStatus() Integrity {
	if v.Verify() {
		return IntegritySealed
	}
	return IntegrityUnsealed
}

func (v ContextView) InputRef() string {
	if value, ok := v.Invocation["input_ref"].(string); ok {
		return value
	}
	return ""
}

func (v ContextView) InvocationID() string {
	if value, ok := v.Invocation["invocation_id"].(string); ok {
		return value
	}
	return ""
}

func (v ContextView) EngineVersion() string {
	engine, ok := v.Invocation["engine"].(map[string]any)
	if !ok {
		return ""
	}
	value, _ := engine["engine_version"].(string)
	return value
}

func (v ContextView) CapabilityVersion() string {
	operation, ok := v.Invocation["operation"].(map[string]any)
	if !ok {
		return ""
	}
	value, _ := operation["capability_version"].(string)
	return value
}

func (v ContextView) RequireText() (string, error) {
	if v.Text == nil {
		return "", NewEngineExecutionError("Engine view has no text", nil, &v)
	}
	return *v.Text, nil
}

func (v ContextView) RecoveryBinding() map[string]string {
	if v.RecoveryRef == "" {
		return nil
	}
	return map[string]string{"recovery_ref": v.RecoveryRef, "source_ref": v.SourceRef, "source_digest": v.SourceDigest}
}

func (v ContextView) Verify() bool {
	if v.Status != EngineStatusSucceeded && v.Status != EngineStatusDegraded {
		return false
	}
	if v.RecoveryRef == "" || v.OutputRef == nil || v.OutputDigest == nil || v.Text == nil {
		return false
	}
	lineage, ok := v.Invocation["source_refs"].([]any)
	if !ok {
		if refs, okStrings := v.Invocation["source_refs"].([]string); okStrings {
			ok = len(refs) > 0
			for _, ref := range refs {
				if ref == v.SourceRef {
					return observationMatches(v)
				}
			}
			return false
		}
		return false
	}
	found := false
	for _, item := range lineage {
		if ref, ok := item.(string); ok && ref == v.SourceRef {
			found = true
		}
	}
	return found && observationMatches(v)
}

func observationMatches(v ContextView) bool {
	if v.Observation["invocation_id"] != v.InvocationID() || v.Observation["output_digest"] != *v.OutputDigest || v.Observation["output_ref"] != *v.OutputRef {
		return false
	}
	return v.ReceiptLink != nil && v.ReceiptLink.InvocationID == v.InvocationID()
}

func (v ContextView) ToDict() map[string]any {
	measurements := make([]any, len(v.Measurements))
	for i, item := range v.Measurements {
		measurements[i] = item.ToDict()
	}
	var failure any
	if v.Failure != nil {
		failure = v.Failure.ToDict()
	}
	var link any
	if v.ReceiptLink != nil {
		link = v.ReceiptLink.ToDict()
	}
	invocation, _ := cloneMap(v.Invocation)
	observation, _ := cloneMap(v.Observation)
	return map[string]any{"schema_version": int64(v.SchemaVersion), "transport_version": int64(v.TransportVersion), "engine_interface_version": v.EngineInterfaceVersion, "source": v.Source.ToDict(), "text": pointerValue(v.Text), "output_ref": pointerValue(v.OutputRef), "output_digest": pointerValue(v.OutputDigest), "source_ref": v.SourceRef, "source_digest": v.SourceDigest, "recovery_ref": nullableString(v.RecoveryRef), "status": string(v.Status), "measurements": measurements, "failure": failure, "receipt_link": link, "invocation": invocation, "observation": observation}
}

func pointerValue(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func nullableString(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func (v ContextView) MarshalJSON() ([]byte, error) { return json.Marshal(v.ToDict()) }

func sha256Hex(data []byte) string {
	hash := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(hash[:])
}

func planRef(value string) error {
	if !planPattern.MatchString(value) {
		return NewValidationError("plan_id must be a deterministic plan reference")
	}
	return nil
}
