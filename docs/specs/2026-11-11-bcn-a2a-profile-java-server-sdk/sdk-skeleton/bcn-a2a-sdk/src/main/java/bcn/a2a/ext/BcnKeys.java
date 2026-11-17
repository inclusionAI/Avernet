package bcn.a2a.ext;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 平台保留的 metadata 键规则、扩展 URI 和协议版本。所有地方都引用这里的常量，不要在别处手写字符串。
 *
 * <p>metadata 的键分三类：以 {@code bcn_} 开头的（以及裸的 {@code bcn}）归平台；含冒号的 URI 形式归外部标准扩展；
 * 其余都归业务。用下划线而不用点号，是为了避免 JSON 路径、日志检索、按路径读写的工具库把键拆成层级。
 *
 * <p>本期平台数据有两项：
 * <ul>
 *   <li>调用上下文：固定字段 {@link #CALLER}，不需要协商，带了就解析；</li>
 *   <li>工具观察（toolcall）：唯一的 URI 扩展 {@link #EXT_TOOLS_V2}，需要协商；数据在状态消息的 DataPart 中，不占用 metadata 键。</li>
 * </ul>
 *
 * <p>TODO(待定)：扩展 URI 的命名方式（Q2、HQ-9），下面的 URI 是占位。
 */
public final class BcnKeys {

    private BcnKeys() {
    }

    /** 平台键前缀。业务不得使用以它开头的键，也不得使用裸的 {@link #ROOT}。 */
    public static final String PREFIX = "bcn_";
    public static final String ROOT = "bcn";

    /** 请求级 metadata：调用上下文。 */
    public static final String CALLER = PREFIX + "caller";

    /** 工具观察扩展（toolcall）的 URI（占位）。 */
    public static final String EXT_TOOLS_V2 = "urn:bcn:a2a:extension:tools:v2";

    /** A2A 线协议版本（与 a2a-java 依赖版本无关）。 */
    public static final String PROTOCOL_VERSION = "1.0";

    /** 是否为平台或外部扩展保留的键：{@code bcn}、{@code bcn_*}，以及含冒号的 URI 形式的键。其余键都属于业务。 */
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
