package bcn.a2a.spi;

/**
 * 业务主动拒绝请求时抛出。message 会返回给调用方，不得包含敏感信息。
 * 其他非 A2A 异常一律视为内部错误（INTERNAL），异常信息不返回给调用方。
 */
public class BcnException extends RuntimeException {

    public enum Code {
        /** 请求内容不满足业务要求，例如缺少必要信息。映射为 INVALID_ARGUMENT。 */
        INVALID_INPUT,
        /** 内容类型不支持。映射为 A2A 的 ContentTypeNotSupportedError。 */
        UNSUPPORTED_CONTENT,
        /** 业务暂时无法处理（依赖不可用、限流）。映射为 UNAVAILABLE，调用方可以稍后重试。 */
        UNAVAILABLE
    }

    private final Code code;

    public BcnException(Code code, String message) {
        super(message);
        this.code = code;
    }

    public Code code() {
        return code;
    }
}
