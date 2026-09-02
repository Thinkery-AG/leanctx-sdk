package com.thinkery.leanctx;

import java.io.ByteArrayOutputStream;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Internal strict JSON codec shared by all public protocol boundaries. */
final class Json {
    static final long MAX_SAFE_INTEGER = 9_007_199_254_740_991L;
    private static final BigInteger MAX_SAFE = BigInteger.valueOf(MAX_SAFE_INTEGER);
    private static final Comparator<String> CODE_POINT_ORDER = Json::compareCodePoints;

    private Json() {
    }

    static byte[] utf8(String value, String field) {
        if (value == null) {
            throw new ValidationError(field + " must be a string");
        }
        validateUnicode(value, field);
        return value.getBytes(StandardCharsets.UTF_8);
    }

    static void validateUnicode(String value, String field) {
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (Character.isHighSurrogate(c)) {
                if (i + 1 >= value.length() || !Character.isLowSurrogate(value.charAt(i + 1))) {
                    throw new ValidationError(field + " is not valid UTF-8");
                }
                i++;
            } else if (Character.isLowSurrogate(c)) {
                throw new ValidationError(field + " is not valid UTF-8");
            }
        }
    }

    static String canonical(Object value) {
        StringBuilder out = new StringBuilder();
        writeCanonical(value, out, new IdentityHashMap<>());
        return out.toString();
    }

    static byte[] canonicalBytes(Object value) {
        return canonical(value).getBytes(StandardCharsets.UTF_8);
    }

    private static void writeCanonical(Object value, StringBuilder out,
                                       IdentityHashMap<Object, Boolean> active) {
        value = plain(value);
        if (value == null) {
            out.append("null");
        } else if (value instanceof String string) {
            validateUnicode(string, "JSON string");
            writeString(string, out);
        } else if (value instanceof Boolean bool) {
            out.append(bool ? "true" : "false");
        } else if (value instanceof Number number) {
            out.append(number(number));
        } else if (value instanceof Map<?, ?> map) {
            enter(value, active);
            try {
                List<String> keys = new ArrayList<>();
                for (Object key : map.keySet()) {
                    if (!(key instanceof String stringKey)) {
                        throw new ValidationError("canonical JSON object keys must be strings");
                    }
                    validateUnicode(stringKey, "canonical JSON key");
                    keys.add(stringKey);
                }
                keys.sort(CODE_POINT_ORDER);
                out.append('{');
                for (int i = 0; i < keys.size(); i++) {
                    if (i > 0) {
                        out.append(',');
                    }
                    String key = keys.get(i);
                    writeString(key, out);
                    out.append(':');
                    writeCanonical(map.get(key), out, active);
                }
                out.append('}');
            } finally {
                active.remove(value);
            }
        } else if (value instanceof Collection<?> collection) {
            enter(value, active);
            try {
                out.append('[');
                int index = 0;
                for (Object item : collection) {
                    if (index++ > 0) {
                        out.append(',');
                    }
                    writeCanonical(item, out, active);
                }
                out.append(']');
            } finally {
                active.remove(value);
            }
        } else if (value.getClass().isArray()) {
            enter(value, active);
            try {
                out.append('[');
                int length = java.lang.reflect.Array.getLength(value);
                for (int i = 0; i < length; i++) {
                    if (i > 0) {
                        out.append(',');
                    }
                    writeCanonical(java.lang.reflect.Array.get(value, i), out, active);
                }
                out.append(']');
            } finally {
                active.remove(value);
            }
        } else {
            throw new ValidationError("value is not canonical JSON data");
        }
    }

    private static void enter(Object value, IdentityHashMap<Object, Boolean> active) {
        if (active.put(value, Boolean.TRUE) != null) {
            throw new ValidationError("value is not canonical JSON data");
        }
    }

    private static String number(Number number) {
        BigInteger integer;
        if (number instanceof Byte || number instanceof Short || number instanceof Integer
                || number instanceof Long || number instanceof BigInteger) {
            integer = new BigInteger(number.toString());
        } else if (number instanceof BigDecimal decimal) {
            if (decimal.signum() == 0 && decimal.toString().startsWith("-")) {
                throw new ValidationError("canonical JSON numbers cannot be negative zero");
            }
            try {
                integer = decimal.toBigIntegerExact();
            } catch (ArithmeticException exception) {
                throw new ValidationError("canonical JSON numbers must be safe integers", exception);
            }
        } else if (number instanceof Double || number instanceof Float) {
            double floating = number.doubleValue();
            if (!Double.isFinite(floating) || (floating == 0.0d
                    && Double.doubleToRawLongBits(floating) < 0)) {
                throw new ValidationError("canonical JSON numbers must be safe integers");
            }
            try {
                integer = BigDecimal.valueOf(floating).toBigIntegerExact();
            } catch (ArithmeticException exception) {
                throw new ValidationError("canonical JSON numbers must be safe integers", exception);
            }
        } else {
            throw new ValidationError("canonical JSON numbers must be safe integers");
        }
        if (integer.compareTo(MAX_SAFE.negate()) < 0 || integer.compareTo(MAX_SAFE) > 0) {
            throw new ValidationError("canonical JSON numbers must be safe integers");
        }
        return integer.toString();
    }

    private static void writeString(String value, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        out.append('"');
    }

    static Object parse(byte[] data, String label) {
        String source;
        try {
            source = decodeUtf8(data);
        } catch (CharacterCodingException exception) {
            throw new EngineProtocolError("invalid " + label, exception);
        }
        Parser parser = new Parser(source, label);
        Object result = parser.parse();
        if (!(result instanceof Map<?, ?>)) {
            throw new EngineProtocolError(label + " must be a JSON object");
        }
        return result;
    }

    private static String decodeUtf8(byte[] data) throws CharacterCodingException {
        return StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(data)).toString();
    }

    private static int compareCodePoints(String left, String right) {
        int[] a = left.codePoints().toArray();
        int[] b = right.codePoints().toArray();
        int length = Math.min(a.length, b.length);
        for (int i = 0; i < length; i++) {
            if (a[i] != b[i]) {
                return Integer.compare(a[i], b[i]);
            }
        }
        return Integer.compare(a.length, b.length);
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map<?, ?> map)) {
            throw new EngineProtocolError(label + " must be an object");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            if (!(entry.getKey() instanceof String key)) {
                throw new EngineProtocolError(label + " has a non-string field");
            }
            result.put(key, entry.getValue());
        }
        return result;
    }

    static void exactKeys(Map<String, Object> value, Set<String> expected, String label) {
        if (!value.keySet().equals(expected)) {
            throw new EngineProtocolError(label + " fields do not match the v1 contract");
        }
    }

    static long integer(Object value, String field) {
        if (value instanceof Boolean || !(value instanceof Number number)) {
            throw new EngineProtocolError(field + " must be an integer");
        }
        BigInteger integer;
        try {
            if (number instanceof BigInteger) {
                integer = (BigInteger) number;
            } else if (number instanceof BigDecimal decimal) {
                integer = decimal.toBigIntegerExact();
            } else if (number instanceof Double || number instanceof Float) {
                integer = BigDecimal.valueOf(number.doubleValue()).toBigIntegerExact();
            } else {
                integer = new BigInteger(number.toString());
            }
        } catch (ArithmeticException exception) {
            throw new EngineProtocolError(field + " must be an integer", exception);
        }
        if (integer.compareTo(MAX_SAFE.negate()) < 0 || integer.compareTo(MAX_SAFE) > 0) {
            throw new EngineProtocolError(field + " must be a safe integer");
        }
        return integer.longValueExact();
    }

    static String string(Object value, String field, int maximumBytes) {
        if (!(value instanceof String string)) {
            throw new EngineProtocolError(field + " must be a string");
        }
        validateUnicode(string, field);
        if (string.getBytes(StandardCharsets.UTF_8).length > maximumBytes || string.indexOf('\0') >= 0) {
            throw new EngineProtocolError(field + " violates its bound");
        }
        return string;
    }

    static String requiredRef(Object value, String field) {
        String ref = string(value, field, 512);
        if (ref.isEmpty() || ref.chars().anyMatch(c -> c < 0x20 || c > 0x7e)) {
            throw new EngineProtocolError(field + " must be printable ASCII");
        }
        return ref;
    }

    static String digest(Object value, String field) {
        String digest = requiredRef(value, field);
        if (!digest.matches("sha256:[0-9a-f]{64}")) {
            throw new EngineProtocolError(field + " must be sha256:<64 lowercase hex>");
        }
        return digest;
    }

    static String optionalDigest(Object value, String field) {
        return value == null ? null : digest(value, field);
    }

    static String outputRef(Object value, String field) {
        String ref = requiredRef(value, field);
        if (!ref.matches("output:[0-9a-f]{64}")) {
            throw new EngineProtocolError(field + " must be output:<64 lowercase hex>");
        }
        return ref;
    }

    static String optionalOutputRef(Object value, String field) {
        return value == null ? null : outputRef(value, field);
    }

    static Object plain(Object value) {
        return plain(value, new IdentityHashMap<>());
    }

    private static Object plain(Object value, IdentityHashMap<Object, Boolean> active) {
        if (value == null || value instanceof String || value instanceof Boolean || value instanceof Number) {
            return value;
        }
        if (value instanceof ContextSource source) {
            return source.toMap();
        }
        if (value instanceof ContextPlan plan) {
            return plan.toMap();
        }
        if (value instanceof ContextMeasurement measurement) {
            return measurement.toMap();
        }
        if (value instanceof ContextFailure failure) {
            return failure.toMap();
        }
        if (value instanceof ContextReceiptLink link) {
            return link.toMap();
        }
        if (value instanceof RecoveredSource source) {
            return source.toMap();
        }
        if (value instanceof ContextView view) {
            return view.toMap();
        }
        if (value instanceof Enum<?> enumValue) {
            if (enumValue instanceof HasValue hasValue) {
                return hasValue.value();
            }
            return enumValue.name();
        }
        if (value instanceof Map<?, ?> map) {
            enter(value, active);
            try {
                Map<String, Object> result = new LinkedHashMap<>();
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    if (!(entry.getKey() instanceof String key)) {
                        throw new ValidationError("JSON object keys must be strings");
                    }
                    result.put(key, plain(entry.getValue(), active));
                }
                return result;
            } finally {
                active.remove(value);
            }
        }
        if (value instanceof Iterable<?> iterable) {
            enter(value, active);
            try {
                List<Object> result = new ArrayList<>();
                for (Object item : iterable) {
                    result.add(plain(item, active));
                }
                return result;
            } finally {
                active.remove(value);
            }
        }
        if (value.getClass().isArray()) {
            enter(value, active);
            try {
                List<Object> result = new ArrayList<>();
                int length = java.lang.reflect.Array.getLength(value);
                for (int i = 0; i < length; i++) {
                    result.add(plain(java.lang.reflect.Array.get(value, i), active));
                }
                return result;
            } finally {
                active.remove(value);
            }
        }
        throw new ValidationError("value is not deterministic JSON data");
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> immutableMap(Map<String, ?> value) {
        Map<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<String, ?> entry : value.entrySet()) {
            if (entry.getKey() == null) {
                throw new ValidationError("JSON object keys must not be null");
            }
            copy.put(entry.getKey(), immutable(entry.getValue()));
        }
        return Collections.unmodifiableMap(copy);
    }

    static Map<String, Object> immutableMapPreserving(Map<String, ?> value) {
        Map<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<String, ?> entry : value.entrySet()) {
            if (entry.getKey() == null) {
                throw new ValidationError("JSON object keys must not be null");
            }
            copy.put(entry.getKey(), immutablePreserving(entry.getValue()));
        }
        return Collections.unmodifiableMap(copy);
    }

    static Object immutablePreserving(Object value) {
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> typed = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String key)) {
                    throw new ValidationError("JSON object keys must be strings");
                }
                typed.put(key, immutablePreserving(entry.getValue()));
            }
            return Collections.unmodifiableMap(typed);
        }
        if (value instanceof List<?> list) {
            List<Object> typed = new ArrayList<>();
            for (Object item : list) {
                typed.add(immutablePreserving(item));
            }
            return Collections.unmodifiableList(typed);
        }
        if (value instanceof Collection<?> collection) {
            List<Object> typed = new ArrayList<>();
            for (Object item : collection) {
                typed.add(immutablePreserving(item));
            }
            return Collections.unmodifiableList(typed);
        }
        return value;
    }

    static Object immutable(Object value) {
        Object plain = plain(value);
        if (plain instanceof Map<?, ?> map) {
            Map<String, Object> typed = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                typed.put((String) entry.getKey(), immutable(entry.getValue()));
            }
            return Collections.unmodifiableMap(typed);
        }
        if (plain instanceof List<?> list) {
            List<Object> typed = new ArrayList<>();
            for (Object item : list) {
                typed.add(immutable(item));
            }
            return Collections.unmodifiableList(typed);
        }
        return plain;
    }

    private static final class Parser {
        private final String source;
        private final String label;
        private int index;

        private Parser(String source, String label) {
            this.source = source;
            this.label = label;
        }

        private Object parse() {
            skipWhitespace();
            Object value = parseValue();
            skipWhitespace();
            if (index != source.length()) {
                fail("trailing data");
            }
            return value;
        }

        private Object parseValue() {
            if (index >= source.length()) {
                fail("unexpected end of input");
            }
            char c = source.charAt(index);
            return switch (c) {
                case '{' -> parseObject();
                case '[' -> parseArray();
                case '"' -> parseString();
                case 't' -> take("true", Boolean.TRUE);
                case 'f' -> take("false", Boolean.FALSE);
                case 'n' -> take("null", null);
                default -> {
                    if (c == '-' || Character.isDigit(c)) {
                        yield parseNumber();
                    }
                    fail("invalid value");
                    yield null;
                }
            };
        }

        private Map<String, Object> parseObject() {
            index++;
            Map<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            if (takeChar('}')) {
                return result;
            }
            while (true) {
                skipWhitespace();
                if (index >= source.length() || source.charAt(index) != '"') {
                    fail("object key must be a string");
                }
                String key = parseString();
                if (result.containsKey(key)) {
                    fail("duplicate key: " + key);
                }
                skipWhitespace();
                if (!takeChar(':')) {
                    fail("object key missing colon");
                }
                skipWhitespace();
                result.put(key, parseValue());
                skipWhitespace();
                if (takeChar('}')) {
                    return result;
                }
                if (!takeChar(',')) {
                    fail("object missing comma");
                }
            }
        }

        private List<Object> parseArray() {
            index++;
            List<Object> result = new ArrayList<>();
            skipWhitespace();
            if (takeChar(']')) {
                return result;
            }
            while (true) {
                skipWhitespace();
                result.add(parseValue());
                skipWhitespace();
                if (takeChar(']')) {
                    return result;
                }
                if (!takeChar(',')) {
                    fail("array missing comma");
                }
            }
        }

        private String parseString() {
            if (!takeChar('"')) {
                fail("string must begin with a quote");
            }
            StringBuilder result = new StringBuilder();
            while (index < source.length()) {
                char c = source.charAt(index++);
                if (c == '"') {
                    String value = result.toString();
                    try {
                        validateUnicode(value, label + " string");
                    } catch (ValidationError error) {
                        fail(error.getMessage());
                    }
                    return value;
                }
                if (c == '\\') {
                    if (index >= source.length()) {
                        fail("unterminated string escape");
                    }
                    char escaped = source.charAt(index++);
                    switch (escaped) {
                        case '"' -> result.append('"');
                        case '\\' -> result.append('\\');
                        case '/' -> result.append('/');
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(parseUnicodeEscape());
                        default -> fail("invalid string escape");
                    }
                } else {
                    if (c < 0x20) {
                        fail("control character in string");
                    }
                    result.append(c);
                }
            }
            fail("unterminated string");
            return null;
        }

        private char parseUnicodeEscape() {
            if (index + 4 > source.length()) {
                fail("invalid unicode escape");
            }
            String hex = source.substring(index, index + 4);
            if (!hex.matches("[0-9a-fA-F]{4}")) {
                fail("invalid unicode escape");
            }
            index += 4;
            return (char) Integer.parseInt(hex, 16);
        }

        private Number parseNumber() {
            int start = index;
            if (source.charAt(index) == '-') {
                index++;
            }
            if (index >= source.length()) {
                fail("invalid number");
            }
            if (source.charAt(index) == '0') {
                index++;
                if (index < source.length() && Character.isDigit(source.charAt(index))) {
                    fail("leading zero in number");
                }
            } else if (source.charAt(index) >= '1' && source.charAt(index) <= '9') {
                while (index < source.length() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
            } else {
                fail("invalid number");
            }
            boolean decimal = false;
            if (index < source.length() && source.charAt(index) == '.') {
                decimal = true;
                index++;
                int fractionStart = index;
                while (index < source.length() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
                if (fractionStart == index) {
                    fail("invalid number fraction");
                }
            }
            if (index < source.length() && (source.charAt(index) == 'e' || source.charAt(index) == 'E')) {
                decimal = true;
                index++;
                if (index < source.length() && (source.charAt(index) == '+' || source.charAt(index) == '-')) {
                    index++;
                }
                int exponentStart = index;
                while (index < source.length() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
                if (exponentStart == index) {
                    fail("invalid number exponent");
                }
            }
            String number = source.substring(start, index);
            try {
                return decimal ? new BigDecimal(number) : new BigInteger(number);
            } catch (NumberFormatException exception) {
                fail("invalid number");
                return BigInteger.ZERO;
            }
        }

        private Object take(String literal, Object value) {
            if (!source.startsWith(literal, index)) {
                fail("invalid value");
            }
            index += literal.length();
            return value;
        }

        private boolean takeChar(char expected) {
            if (index < source.length() && source.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void skipWhitespace() {
            while (index < source.length()) {
                char c = source.charAt(index);
                if (c != ' ' && c != '\t' && c != '\r' && c != '\n') {
                    return;
                }
                index++;
            }
        }

        private void fail(String message) {
            throw new EngineProtocolError("invalid " + label + ": " + message);
        }
    }
}
