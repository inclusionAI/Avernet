package bcn.a2a.spi;

import org.a2aproject.sdk.spec.CancelTaskParams;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskIdParams;
import org.a2aproject.sdk.spec.TaskQueryParams;

/**
 * 第三档（引擎模式）：由引擎的运行记录支撑任务查询、订阅和取消。与 {@link BcnAgent#onStream} 一起实现。
 *
 * <p>对引擎的要求（上位稿 R-01–R-09）：Task 状态的唯一来源是引擎的运行记录（D3）；
 * 任何实例都能读取和续写同一个 Task；停机恢复由引擎保证（D4）。SDK 不保存任何 Task 状态。
 *
 * <p>这三个请求不带调用上下文（P-104），按 Task 本身的归属处理。
 */
public interface TaskEngine {

    /**
     * GetTask：返回 Task 当前快照。
     * 不存在时抛 {@code TaskNotFoundError}；按 {@code historyLength} 截断历史由引擎负责。
     */
    Task getTask(TaskQueryParams params) throws Exception;

    /**
     * SubscribeToTask：先用 {@link BcnStream#begin} 发出当前快照，再持续输出后续事件，直到终态或中断态。
     * 不存在时抛 {@code TaskNotFoundError}；已经是终态时抛 {@code UnsupportedOperationError}（A2A 规定）。
     */
    void subscribe(TaskIdParams params, BcnStream out) throws Exception;

    /**
     * CancelTask：发起取消并返回取消后的快照。
     * 不存在时抛 {@code TaskNotFoundError}；不能取消时抛 {@code TaskNotCancelableError}。
     * 返回的状态不是 CANCELED 时，调用方会视为“取消未确认”。
     */
    Task cancel(CancelTaskParams params) throws Exception;
}
