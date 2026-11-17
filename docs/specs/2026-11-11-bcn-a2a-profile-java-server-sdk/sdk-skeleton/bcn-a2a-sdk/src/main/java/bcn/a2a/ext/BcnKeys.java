package bcn.a2a.ext;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 平台保留的 metadata 键、扩展 URI 和绑定名。所有地方都引用这里的常量，不要在别处手写字符串。
 *
 * <p>TODO(待定)：
 * <ul>
 *   <li>键名用 {@code bcn.} 还是 {@code bcn_}（带点号的键在 JSON 路径、日志检索、按路径读写的工具库里需要转义）。只改 {@link #PREFIX}。</li>
 *   <li>扩展 URI 的命名方式（Q2、HQ-9）。下面的 URI 都是占位。</li>
 * </ul>
 */
public final class BcnKeys {

    private BcnKeys() {
    }

    /** 平台键前缀。业务不得使用以它开头的键，也不得使用裸的 {@link #ROOT}。 */
    public static final String PREFIX = "bcn.";
    public static final String ROOT = "bcn";

    /** 请求级 metadata：调用上下文。 */
    public static final String CALLER = PREFIX + "caller";
    /** 请求级 metadata：hitl/v1 的额外类型声明；Task.metadata：hitl/v1 回执。 */
    public static final String HITL = PREFIX + "hitl";

    /** 平台扩展的 URI（占位）。 */
    public static final String EXT_CALLER = "urn:bcn:a2a:extension:caller:v1";
    public static final String EXT_TOOLS_V2 = "urn:bcn:a2a:extension:tools:v2";
    public static final String EXT_HITL_V1 = "urn:bcn:a2a:extension:hitl:v1";

    /** A2A 线协议版本（与 a2a-java 依赖版本无关）。 */
    public static final String PROTOCOL_VERSION = "1.0";

    /**
     * 是否为平台或扩展保留的键：{@code bcn}、{@code bcn.*}，以及 URI 形式的键（含冒号，留给外部标准扩展）。
     * 其余键都属于业务。
     */
    public static boolean isReserved(String key) {
        return key.equals(ROOT) || key.startsWith(PREFIX) || key.indexOf(':') >= 0;
    }

    /** 返回 metadata 中的业务字段视图（去掉保留键），只读；入参为 null 时返回空 Map。 */
    public static Map<String, Object> businessView(Map<String, Object> metadata) {
        if (metadata == null || metadata.isEmpty()) {
            return Map.of();
        }
        Map<String, Object> view = new LinkedHashMap<>();
        metadata.forEach((key, value) -> {
            if (!isReserved(key)) {
                view.put(key, value);
            }
        });
        return Collections.unmodifiableMap(view);
    }
}
