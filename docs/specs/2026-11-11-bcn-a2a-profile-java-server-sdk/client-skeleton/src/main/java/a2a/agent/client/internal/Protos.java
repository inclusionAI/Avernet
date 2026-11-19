package a2a.agent.client.internal;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.google.protobuf.ListValue;
import com.google.protobuf.NullValue;
import com.google.protobuf.Struct;
import com.google.protobuf.Value;

/** Java 对象与 protobuf Struct / Value 的互转（metadata、工具记录的 data）。 */
public final class Protos {

    /** 2^53 - 1：Struct 中的数字是 double，超过它的整数会丢精度。 */
    private static final long MAX_SAFE_INTEGER = 9_007_199_254_740_991L;

    private Protos() {
    }

    public static Struct toStruct(Map<String, ?> map) {
        Struct.Builder builder = Struct.newBuilder();
        map.forEach((k, v) -> builder.putFields(k, toValue(v)));
        return builder.build();
    }

    /** 不支持的类型、超过 2^53 的整数抛 IllegalArgumentException（Payload 规范要求大整数和金额用字符串）。 */
    public static Value toValue(Object o) {
        if (o == null) {
            return Value.newBuilder().setNullValue(NullValue.NULL_VALUE).build();
        }
        if (o instanceof String s) {
            return Value.newBuilder().setStringValue(s).build();
        }
        if (o instanceof Boolean b) {
            return Value.newBuilder().setBoolValue(b).build();
        }
        if (o instanceof Long || o instanceof Integer || o instanceof Short || o instanceof Byte) {
            long v = ((Number) o).longValue();
            if (Math.abs(v) > MAX_SAFE_INTEGER) {
                throw new IllegalArgumentException("integer beyond 2^53 must be sent as a string: " + v);
            }
            return Value.newBuilder().setNumberValue(v).build();
        }
        if (o instanceof BigInteger || o instanceof BigDecimal) {
            throw new IllegalArgumentException("big numbers and amounts must be sent as strings: " + o);
        }
        if (o instanceof Number n) {
            return Value.newBuilder().setNumberValue(n.doubleValue()).build();
        }
        if (o instanceof Map<?, ?> m) {
            Struct.Builder builder = Struct.newBuilder();
            m.forEach((k, v) -> builder.putFields(String.valueOf(k), toValue(v)));
            return Value.newBuilder().setStructValue(builder).build();
        }
        if (o instanceof Iterable<?> it) {
            ListValue.Builder list = ListValue.newBuilder();
            it.forEach(item -> list.addValues(toValue(item)));
            return Value.newBuilder().setListValue(list).build();
        }
        throw new IllegalArgumentException("unsupported metadata value type: " + o.getClass().getName());
    }

    /** 数字一律返回 Double（线上本来就是 double）。 */
    public static Object toJava(Value v) {
        return switch (v.getKindCase()) {
            case NULL_VALUE, KIND_NOT_SET -> null;
            case NUMBER_VALUE -> v.getNumberValue();
            case STRING_VALUE -> v.getStringValue();
            case BOOL_VALUE -> v.getBoolValue();
            case STRUCT_VALUE -> toJavaMap(v.getStructValue());
            case LIST_VALUE -> {
                List<Object> list = new ArrayList<>(v.getListValue().getValuesCount());
                v.getListValue().getValuesList().forEach(item -> list.add(toJava(item)));
                yield Collections.unmodifiableList(list);
            }
        };
    }

    public static Map<String, Object> toJavaMap(Struct s) {
        Map<String, Object> map = new LinkedHashMap<>();
        s.getFieldsMap().forEach((k, v) -> map.put(k, toJava(v)));
        return Collections.unmodifiableMap(map);
    }
}
