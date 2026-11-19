package a2a.agent.client.internal;

/** Payload 规范中的固定值：平台保留键、toolcall 扩展 URI。 */
public final class PayloadKeys {

    private PayloadKeys() {
    }

    /** 请求级 metadata：调用上下文（Payload 规范 7.3）。 */
    public static final String CALLER = "bcn_caller";

    /** 平台键前缀；业务键不得使用，也不得使用裸的 {@link #ROOT}。 */
    public static final String PREFIX = "bcn_";
    public static final String ROOT = "bcn";

    /** toolcall 扩展的 URI。占位，待定（Payload 规范第 14 节），必须与服务端 Adapter 使用同一个值。 */
    public static final String TOOLCALL_EXTENSION = "urn:bcn:a2a:extension:tools:v2";

    /** 平台键（bcn、bcn_*）和外部扩展的 URI 键（含冒号）都不允许业务写入。 */
    public static boolean isReserved(String key) {
        return key.equals(ROOT) || key.startsWith(PREFIX) || key.indexOf(':') >= 0;
    }
}
