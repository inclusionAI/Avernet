package bcn.a2a.spi;

import org.a2aproject.sdk.spec.Message;

/**
 * 业务实现的 Agent。一个实现类就是一个 Agent，用 {@link A2aAgent} 注解声明身份。
 *
 * <p><b>三档接入</b>（onMessage 和 onStream 只能覆盖其中一个，启动时检查）：
 * <ol>
 *   <li><b>一次性回复</b>：覆盖 {@link #onMessage}。SendMessage 返回这条消息；
 *       SendStreamingMessage 的流中只有这一条消息（A2A 规定：没有 Task 的流恰好一条 Message）。</li>
 *   <li><b>流式输出</b>：覆盖 {@link #onStream}。SDK 生成 Task 骨架，把输出转成标准事件。
 *       同一个实现也服务非流式的 SendMessage：SDK 收集全部输出后返回最终 Task。
 *       Task 只存在于这条流内，GetTask / SubscribeToTask / CancelTask 返回 TASK_NOT_FOUND。</li>
 *   <li><b>引擎模式</b>：覆盖 onStream，并实现 {@link TaskEngine}。查询、订阅、取消和续交由引擎的运行记录支撑。</li>
 * </ol>
 *
 * <p><b>运行约定</b>（两个方法都适用）：
 * <ul>
 *   <li>在业务提供给 SDK 的线程池中执行，可以阻塞；每个请求只调用一次。</li>
 *   <li>执行时间较长时应检查 {@link BcnCall#isCancelled()}，调用方取消或超时后尽快返回。</li>
 *   <li>异常：抛 {@link BcnException} 表示业务拒绝，映射为对应错误码；抛 a2a-java 的 {@code A2AError}
 *       子类按 A2A 标准映射；其他异常一律映射为 INTERNAL，异常信息只记日志，不返回给调用方。</li>
 *   <li>不需要处理协议细节（协商、校验、错误码、Task 状态机），这些由 SDK 负责。</li>
 * </ul>
 */
public interface BcnAgent {

    /**
     * 第一档：收到一条消息，返回一条完整回复。
     *
     * <p>返回值要求：role 为 AGENT；只含文本 Part（第一期）；不设置 taskId。
     * contextId 可以不填，SDK 会填成 {@link BcnCall#contextId()}；填了就必须与它相同。
     * 建议用 {@link Messages#agentText(String)} 构造。
     *
     * @param call 本次调用，只读
     * @return 回复，不能为 null
     */
    default Message onMessage(BcnCall call) throws Exception {
        throw new UnsupportedOperationException("onMessage is not implemented");
    }

    /**
     * 第二、三档：通过 {@code out} 逐段输出。
     *
     * <p>方法正常返回时，如果还没调用 {@link BcnStream#complete()} 或 {@link BcnStream#fail(String)}，
     * SDK 按完成处理；抛出异常时，SDK 看 Task 是否已经发出：已发出则发 FAILED 终态并正常结束流，
     * 未发出则直接返回错误。
     *
     * <p>引擎模式下，带 taskId 的续交消息也进入这里，此时 {@link BcnCall#taskId()} 有值。
     * 引擎应先确认 Task 存在（不存在时抛 {@code TaskNotFoundError}），再用 {@link BcnStream#begin} 发出当前快照。
     *
     * @param call 本次调用，只读
     * @param out  输出通道；可以在其他线程中调用，但同一时刻只能有一个线程在写
     */
    default void onStream(BcnCall call, BcnStream out) throws Exception {
        throw new UnsupportedOperationException("onStream is not implemented");
    }
}
