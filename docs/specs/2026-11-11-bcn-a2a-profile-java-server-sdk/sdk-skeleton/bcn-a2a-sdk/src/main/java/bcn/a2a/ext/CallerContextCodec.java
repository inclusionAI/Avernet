package bcn.a2a.ext;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

import bcn.a2a.spi.CallerContext;

/**
 * 调用上下文扩展（{@code bcn.caller}）的解码。只在调用方激活了 {@link BcnKeys#EXT_CALLER} 时使用。
 *
 * <p>数据形如：{@code {"userId": "u-1", "source": {"platform": "bcn"}}}，userId 可以缺省。
 */
public final class CallerContextCodec {

    private CallerContextCodec() {
    }

    /**
     * 从请求级 metadata 解码调用上下文。
     *
     * @return 没有 {@code bcn.caller} 时返回 null
     */
    public static CallerContext decode(Map<String, Object> requestMetadata) {
        if (requestMetadata == null) {
            return null;
        }
        Object raw = requestMetadata.get(BcnKeys.CALLER);
        if (!(raw instanceof Map<?, ?> map)) {
            // TODO(实现)：存在但不是对象时怎么处理——按“入站不认识就保留并忽略”的原则忽略并记 warn，还是拒绝？待定。
            return null;
        }
        Map<String, Object> attributes = new LinkedHashMap<>();
        map.forEach((k, v) -> attributes.put(String.valueOf(k), v));
        String platform = null;
        if (attributes.get("source") instanceof Map<?, ?> source && source.get("platform") instanceof String p) {
            platform = p;
        }
        String userId = attributes.get("userId") instanceof String u && !u.isBlank() ? u : null;
        // TODO(实现)：字段大小上限；required:true 的 Agent 缺少 userId 时拒绝（P-102，第一期不做）。
        return new CallerContext(userId, platform, Collections.unmodifiableMap(attributes));
    }
}
