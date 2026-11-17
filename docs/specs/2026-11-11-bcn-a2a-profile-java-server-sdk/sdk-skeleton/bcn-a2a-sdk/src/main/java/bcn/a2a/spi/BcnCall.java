package bcn.a2a.spi;

import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.MessageSendParams;

/**
 * 一次 SendMessage / SendStreamingMessage 调用。由 SDK 在完成解析、分发、协商和入站校验之后构造，只读。
 *
 * <p>以后增加的信息都以 default 方法加入，已有实现不需要修改。
 */
public interface BcnCall {

    /** 目标 Agent 的 id，即请求中的 tenant。 */
    String agentId();

    /** 原始请求（a2a-java 模型）。一般用不到，优先用下面的便捷方法。 */
    MessageSendParams request();

    /** 请求中的消息，等同于 {@code request().message()}。 */
    Message message();

    /** 消息中所有文本 Part 按顺序拼接的结果，以换行分隔。 */
    String text();

    /** 本次调用的 contextId。请求没有带时由 SDK 生成；回复时沿用。 */
    String contextId();

    /** 续交时的 taskId。只有引擎模式会收到有值的情况；其他档位 SDK 已经提前返回 TASK_NOT_FOUND。 */
    Optional<String> taskId();

    /**
     * 调用上下文（metadata 键 {@code bcn.caller}）。调用方激活了调用上下文扩展、且请求中带了数据时才有值。
     * 字段是调用方的自述信息，不等于经过验证的身份（D1、P-101）。
     */
    Optional<CallerContext> caller();

    /**
     * 请求级 metadata 中的业务字段：已经去掉平台保留键（{@code bcn.*}）和 URI 形式的扩展键。只读。
     * 消息级、Part 级的 metadata 仍在 {@link #message()} 中，SDK 不做处理。
     */
    Map<String, Object> metadata();

    /** 本次调用实际激活的扩展 URI（协商结果）。某个扩展的数据能不能输出，以这里为准。 */
    Set<String> activatedExtensions();

    /** 指定扩展是否已激活。 */
    default boolean isActivated(String extensionUri) {
        return activatedExtensions().contains(extensionUri);
    }

    /** 调用方已经取消，或者已经超过 {@link #deadline()}。业务应尽快返回。 */
    boolean isCancelled();

    /** 本次调用的截止时间：取调用方 deadline 与 SDK 配置超时中较早的一个。 */
    Instant deadline();
}
