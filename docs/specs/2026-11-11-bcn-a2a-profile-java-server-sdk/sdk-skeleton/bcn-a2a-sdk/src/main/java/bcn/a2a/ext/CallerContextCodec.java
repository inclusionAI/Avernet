package bcn.a2a.ext;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

import bcn.a2a.spi.CallerContext;

/**
 * 调用上下文（固定字段 {@code bcn_caller}）的解码。不需要协商：请求级 metadata 中有这个字段就解析。
 *
 * <p>数据形如：{@code {"userId": "u-1", "source": {"platform": "bcn"}}}，userId 可以缺省。
 */
public final class CallerContextCodec {

    private CallerContextCodec() {
    }

    /**
     * 从请求级 metadata 解码调用上下文。
     *
     * @return 没有 {@code bcn_caller} 时返回 null
     */
    public static CallerContext decode(Map<String, Object> requestMetadata) {
        if (requestMetadata == null) {
            return null;
        }
        Object raw = requestMetadata.get(BcnKeys.CALLER);
        if (!(raw instanceof Map<?, ?> map)) {
            // TODO(待定)：字段存在但不是对象时，是按“入站不认识就保留并忽略”忽略并记 warn，还是拒绝请求。
            return null;
        }
        Map<String, Object> attributes = new LinkedHashMap<>();
        map.forEach((k, v) -> attributes.put(String.valueOf(k), v));
        String platform = null;
        if (attributes.get("source") instanceof Map<?, ?> source && source.get("platform") instanceof String p) {
            platform = p;
        }
        String userId = attributes.get("userId") instanceof String u && !u.isBlank() ? u : null;
        // TODO(实现)：字段大小上限。
        return new CallerContext(userId, platform, Collections.unmodifiableMap(attributes));
    }
}
