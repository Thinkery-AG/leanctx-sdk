using System.Collections;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Thinkery.LeanCtx;

internal interface IWireValue
{
    object ToWireValue();
}

internal static class WireJson
{
    internal const int MaxRequestBytes = 64 * 1024;
    internal const int MaxAgentRequestBytes = 1024 * 1024;
    internal const int MaxResponseBytes = 16 * 1024 * 1024;
    internal const int MaxStdErrBytes = 64 * 1024;
    internal const int MaxTextBytes = 8 * 1024 * 1024;
    internal const int MaxPathBytes = 4096;
    internal const int MaxRefBytes = 512;
    internal const int MaxTaskBytes = 16 * 1024;
    internal const int MaxRefs = 32;
    internal const int MaxMeasurements = 32;

    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    internal static byte[] Utf8(string value, string label)
    {
        try
        {
            return StrictUtf8.GetBytes(value);
        }
        catch (Exception error) when (error is EncoderFallbackException or ArgumentException)
        {
            throw new ValidationError($"{label} is not valid UTF-8", error);
        }
    }

    internal static string Text(
        string? value,
        string label,
        int maximum,
        bool controls = true,
        bool allowEmpty = false)
    {
        if (value is null)
            throw new ValidationError($"{label} must be a string");
        var bytes = Utf8(value, label);
        if (!allowEmpty && bytes.Length == 0)
            throw new ValidationError($"{label} must not be empty");
        if (bytes.Length > maximum)
            throw new ValidationError($"{label} exceeds {maximum} UTF-8 bytes");
        if (value.Contains('\0'))
            throw new ValidationError($"{label} contains NUL");
        if (controls && value.Any(character => character < 0x20))
            throw new ValidationError($"{label} contains a control character");
        return value;
    }

    internal static string ValidateRef(string? value, string label = "ref")
    {
        if (value is null)
            throw new ValidationError($"{label} must be a string");
        var bytes = Utf8(value, label);
        if (bytes.Length == 0 || bytes.Length > MaxRefBytes ||
            value.Any(character => character < 0x20 || character > 0x7e))
            throw new ValidationError($"{label} must be 1..{MaxRefBytes} printable ASCII bytes");
        return value;
    }

    internal static string ValidateDigest(string? value, string label = "digest")
    {
        var result = ValidateRef(value, label);
        if (!System.Text.RegularExpressions.Regex.IsMatch(result, "^sha256:[0-9a-f]{64}$"))
            throw new ValidationError($"{label} must be sha256:<64 lowercase hex>");
        return result;
    }

    internal static string ValidateOutputRef(string? value, string label = "output_ref")
    {
        var result = ValidateRef(value, label);
        if (!System.Text.RegularExpressions.Regex.IsMatch(result, "^output:[0-9a-f]{64}$"))
            throw new ValidationError($"{label} must be output:<64 lowercase hex>");
        return result;
    }

    internal static string ValidatePlanRef(string? value, string label = "plan_id")
    {
        var result = ValidateRef(value, label);
        if (!System.Text.RegularExpressions.Regex.IsMatch(result, "^plan:sha256:[0-9a-f]{64}$"))
            throw new ValidationError($"{label} must be a deterministic plan reference");
        return result;
    }

    internal static string Sha256Digest(ReadOnlySpan<byte> data) =>
        $"sha256:{Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant()}";

    internal static string Sha256Digest(string value) => Sha256Digest(Utf8(value, "digest input"));

    internal static byte[] CanonicalBytes(object? value) => Utf8(CanonicalJson(value), "canonical JSON");

    internal static string CanonicalJson(object? value)
    {
        var builder = new StringBuilder();
        WriteCanonical(builder, Plain(value));
        return builder.ToString();
    }

    internal static object? Plain(object? value)
    {
        if (value is null || value is string || value is bool)
            return value;
        if (value is IWireValue wireValue)
            return Plain(wireValue.ToWireValue());
        if (value is JsonDocument document)
            return Plain(document.RootElement);
        if (value is JsonElement element)
            return Plain(element);
        if (value is byte or sbyte or short or ushort or int or uint or long or ulong)
            return Integral(value);
        if (value is float or double or decimal)
            throw new ValidationError("canonical JSON numbers must be safe integers");
        if (value is IDictionary dictionary)
        {
            var result = new Dictionary<string, object?>(StringComparer.Ordinal);
            foreach (var rawKey in dictionary.Keys)
            {
                if (rawKey is not string key)
                    throw new ValidationError("canonical JSON object keys must be strings");
                Utf8(key, "canonical JSON key");
                if (!result.TryAdd(key, Plain(dictionary[rawKey])))
                    throw new ValidationError("canonical JSON object contains duplicate keys");
            }
            return result;
        }
        if (value is IEnumerable<KeyValuePair<string, object?>> pairs)
        {
            var result = new Dictionary<string, object?>(StringComparer.Ordinal);
            foreach (var pair in pairs)
            {
                Utf8(pair.Key, "canonical JSON key");
                if (!result.TryAdd(pair.Key, Plain(pair.Value)))
                    throw new ValidationError("canonical JSON object contains duplicate keys");
            }
            return result;
        }
        if (value is IEnumerable enumerable)
        {
            var result = new List<object?>();
            foreach (var item in enumerable)
                result.Add(Plain(item));
            return result;
        }
        throw new ValidationError("value is not canonical JSON data");
    }

    internal static object? DeepFreeze(object? value)
    {
        if (value is null || value is string || value is bool || value is long)
            return value;
        if (value is IDictionary dictionary)
        {
            var result = new Dictionary<string, object?>(StringComparer.Ordinal);
            foreach (var rawKey in dictionary.Keys)
            {
                if (rawKey is not string key)
                    throw new ValidationError("canonical JSON object keys must be strings");
                result[key] = DeepFreeze(dictionary[rawKey]);
            }
            return new ReadOnlyDictionary<string, object?>(result);
        }
        if (value is IEnumerable enumerable)
        {
            var result = new List<object?>();
            foreach (var item in enumerable)
                result.Add(DeepFreeze(item));
            return new ReadOnlyCollection<object?>(result);
        }
        throw new ValidationError("value is not canonical JSON data");
    }

    private static object? Plain(JsonElement element)
    {
        return element.ValueKind switch
        {
            JsonValueKind.Null => null,
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String => element.GetString(),
            JsonValueKind.Number => Integral(element),
            JsonValueKind.Array => element.EnumerateArray().Select(Plain).ToList(),
            JsonValueKind.Object => ObjectFromJson(element),
            _ => throw new ValidationError("value is not canonical JSON data"),
        };
    }

    private static Dictionary<string, object?> ObjectFromJson(JsonElement element)
    {
        var result = new Dictionary<string, object?>(StringComparer.Ordinal);
        foreach (var property in element.EnumerateObject())
        {
            Utf8(property.Name, "canonical JSON key");
            if (!result.TryAdd(property.Name, Plain(property.Value)))
                throw new ValidationError("canonical JSON object contains duplicate keys");
        }
        return result;
    }

    private static long Integral(JsonElement element)
    {
        var raw = element.GetRawText();
        if (raw == "-0" || !element.TryGetInt64(out var result))
            throw new ValidationError("canonical JSON numbers must be safe integers");
        return result;
    }

    private static long Integral(object value)
    {
        try
        {
            return value switch
            {
                byte item => item,
                sbyte item => item,
                short item => item,
                ushort item => item,
                int item => item,
                uint item => item,
                long item => item,
                ulong item when item <= long.MaxValue => (long)item,
                _ => throw new ValidationError("canonical JSON numbers must be safe integers"),
            };
        }
        catch (OverflowException error)
        {
            throw new ValidationError("canonical JSON numbers must be safe integers", error);
        }
    }

    private static void WriteCanonical(StringBuilder builder, object? value)
    {
        switch (value)
        {
            case null:
                builder.Append("null");
                return;
            case string text:
                AppendJsonString(builder, text);
                return;
            case bool boolean:
                builder.Append(boolean ? "true" : "false");
                return;
            case long integer:
                builder.Append(integer.ToString(CultureInfo.InvariantCulture));
                return;
            case IDictionary dictionary:
                WriteObject(builder, dictionary.Keys.Cast<object?>().Select(key =>
                {
                    var stringKey = key as string ?? throw new ValidationError(
                        "canonical JSON object keys must be strings");
                    return (stringKey, dictionary[stringKey]);
                }));
                return;
            case IEnumerable<KeyValuePair<string, object?>> pairs:
                WriteObject(builder, pairs.Select(pair => (pair.Key, pair.Value)));
                return;
            case IEnumerable enumerable:
                builder.Append('[');
                var first = true;
                foreach (var item in enumerable)
                {
                    if (!first)
                        builder.Append(',');
                    first = false;
                    WriteCanonical(builder, item);
                }
                builder.Append(']');
                return;
            default:
                throw new ValidationError("value is not canonical JSON data");
        }
    }

    private static void WriteObject(
        StringBuilder builder,
        IEnumerable<(string Key, object? Value)> entries)
    {
        var ordered = entries.OrderBy(entry => entry.Key, UnicodeCodePointComparer.Instance).ToList();
        if (ordered.Select(entry => entry.Key).Distinct(StringComparer.Ordinal).Count() != ordered.Count)
            throw new ValidationError("canonical JSON object contains duplicate keys");
        builder.Append('{');
        for (var index = 0; index < ordered.Count; index++)
        {
            if (index > 0)
                builder.Append(',');
            AppendJsonString(builder, ordered[index].Key);
            builder.Append(':');
            WriteCanonical(builder, ordered[index].Value);
        }
        builder.Append('}');
    }

    private static void AppendJsonString(StringBuilder builder, string value)
    {
        _ = Utf8(value, "canonical JSON string");
        builder.Append('"');
        foreach (var rune in value.EnumerateRunes())
        {
            switch (rune.Value)
            {
                case '"': builder.Append("\\\""); break;
                case '\\': builder.Append("\\\\"); break;
                case '\b': builder.Append("\\b"); break;
                case '\f': builder.Append("\\f"); break;
                case '\n': builder.Append("\\n"); break;
                case '\r': builder.Append("\\r"); break;
                case '\t': builder.Append("\\t"); break;
                default:
                    if (rune.Value < 0x20)
                        builder.Append($"\\u{rune.Value:x4}");
                    else
                        builder.Append(rune.ToString());
                    break;
            }
        }
        builder.Append('"');
    }

    internal static Dictionary<string, object?> ParseObject(
        ReadOnlySpan<byte> bytes,
        string label,
        int maximumBytes)
    {
        if (bytes.Length > maximumBytes)
            throw new EngineProtocolError($"{label} exceeds the bound");
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(bytes.ToArray(), new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 128,
            });
            ValidateJson(document.RootElement, new HashSet<string>());
        }
        catch (EngineProtocolError)
        {
            throw;
        }
        catch (Exception error) when (error is JsonException or ArgumentException or DecoderFallbackException)
        {
            throw new EngineProtocolError($"invalid {label}", error);
        }
        if (document.RootElement.ValueKind != JsonValueKind.Object)
            throw new EngineProtocolError($"{label} must be an object");
        return (Dictionary<string, object?>)Plain(document.RootElement)!;
    }

    private static void ValidateJson(JsonElement value, HashSet<string> path)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                var keys = new HashSet<string>(StringComparer.Ordinal);
                foreach (var property in value.EnumerateObject())
                {
                    if (!keys.Add(property.Name))
                        throw new EngineProtocolError("JSON object contains duplicate fields");
                    ValidateJson(property.Value, path);
                }
                break;
            case JsonValueKind.Array:
                foreach (var item in value.EnumerateArray())
                    ValidateJson(item, path);
                break;
            case JsonValueKind.Number:
                _ = Integral(value);
                break;
        }
    }

    internal static void RequireExactKeys(
        IReadOnlyDictionary<string, object?> value,
        IReadOnlySet<string> expected,
        string label)
    {
        if (value.Count != expected.Count || value.Keys.Any(key => !expected.Contains(key)))
            throw new EngineProtocolError($"{label} fields do not match the v1 contract");
    }

    internal static object? Get(IReadOnlyDictionary<string, object?> value, string key) =>
        value.TryGetValue(key, out var item) ? item : null;

    internal static string RequiredString(
        IReadOnlyDictionary<string, object?> value,
        string key,
        int maximum = MaxRefBytes,
        bool allowEmpty = false)
    {
        if (value.GetValueOrDefault(key) is not string result)
            throw new EngineProtocolError($"{key} must be a string");
        try
        {
            return Text(result, key, maximum, controls: false, allowEmpty: allowEmpty);
        }
        catch (ValidationError error)
        {
            throw new EngineProtocolError($"{key} violates its bound", error);
        }
    }

    internal static long RequiredInteger(
        IReadOnlyDictionary<string, object?> value,
        string key)
    {
        if (value.GetValueOrDefault(key) is not long result)
            throw new EngineProtocolError($"{key} must be an integer");
        return result;
    }

    internal static string RequiredRef(
        IReadOnlyDictionary<string, object?> value,
        string key)
    {
        try
        {
            return ValidateRef(value.GetValueOrDefault(key) as string, key);
        }
        catch (ValidationError error)
        {
            throw new EngineProtocolError(error.Message, error);
        }
    }

    internal static string RequiredDigest(
        IReadOnlyDictionary<string, object?> value,
        string key)
    {
        try
        {
            return ValidateDigest(value.GetValueOrDefault(key) as string, key);
        }
        catch (ValidationError error)
        {
            throw new EngineProtocolError(error.Message, error);
        }
    }

    internal static bool RequiredBool(
        IReadOnlyDictionary<string, object?> value,
        string key)
    {
        if (value.GetValueOrDefault(key) is not bool result)
            throw new EngineProtocolError($"{key} must be boolean");
        return result;
    }

    internal static IReadOnlyList<object?> RequiredArray(
        IReadOnlyDictionary<string, object?> value,
        string key,
        int maximum)
    {
        if (value.GetValueOrDefault(key) is not List<object?> result || result.Count > maximum)
            throw new EngineProtocolError($"{key} exceeds its bound");
        return result;
    }

    private sealed class UnicodeCodePointComparer : IComparer<string>
    {
        internal static readonly UnicodeCodePointComparer Instance = new();

        public int Compare(string? left, string? right)
        {
            if (ReferenceEquals(left, right))
                return 0;
            if (left is null)
                return -1;
            if (right is null)
                return 1;
            var leftRunes = left.EnumerateRunes().ToArray();
            var rightRunes = right.EnumerateRunes().ToArray();
            var length = Math.Min(leftRunes.Length, rightRunes.Length);
            for (var index = 0; index < length; index++)
            {
                var difference = leftRunes[index].Value - rightRunes[index].Value;
                if (difference != 0)
                    return difference;
            }
            return leftRunes.Length.CompareTo(rightRunes.Length);
        }
    }
}
