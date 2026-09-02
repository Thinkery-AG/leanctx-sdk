package com.thinkery.leanctx;

import java.util.LinkedHashMap;
import java.util.Map;

/** One bounded Engine measurement. */
public final class ContextMeasurement {
    private final String name;
    private final String unit;
    private final String classification;
    private final Long value;

    public ContextMeasurement(String name, String unit, String classification, Long value) {
        if (name == null || !Protocol.ASCII_NAME.matcher(name).matches()) {
            throw new ValidationError("measurement name must be lowercase ASCII");
        }
        if (unit == null || !Protocol.ASCII_NAME.matcher(unit).matches()) {
            throw new ValidationError("measurement unit must be lowercase ASCII");
        }
        if (!"measured".equals(classification) && !"estimated".equals(classification)
                && !"unavailable".equals(classification)) {
            throw new ValidationError("invalid measurement classification");
        }
        if ("unavailable".equals(classification)) {
            if (value != null) {
                throw new ValidationError("unavailable measurement value must be null");
            }
        } else if (value == null || value < 0) {
            throw new ValidationError("measurement value must be a non-negative integer");
        }
        this.name = name;
        this.unit = unit;
        this.classification = classification;
        this.value = value;
    }

    public ContextMeasurement(String name, String unit, String classification, long value) {
        this(name, unit, classification, Long.valueOf(value));
    }

    public String name() {
        return name;
    }

    public String getName() {
        return name;
    }

    public String unit() {
        return unit;
    }

    public String getUnit() {
        return unit;
    }

    public String classification() {
        return classification;
    }

    public String getClassification() {
        return classification;
    }

    public Long value() {
        return value;
    }

    public Long getValue() {
        return value;
    }

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("name", name);
        result.put("unit", unit);
        result.put("classification", classification);
        result.put("value", value);
        return Json.immutableMap(result);
    }

    public Map<String, Object> toDict() {
        return toMap();
    }

    public Map<String, Object> to_dict() {
        return toMap();
    }
}
