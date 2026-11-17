package bcn.a2a.spi;

import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskStatus;

/**
 * 流式输出通道。业务只调用这些方法，不直接构造 A2A 事件；SDK 负责转成标准事件并保证顺序和状态机正确。
 *
 * <p>事件对应关系：
 * <ul>
 *   <li>第一次输出前，SDK 自动发出 Task 骨架（状态 WORKING）；引擎模式可以先调用 {@link #begin} 指定快照。</li>
 *   <li>{@link #text} → 正文产出物的追加更新（同一个 artifactId，append=true）。</li>
 *   <li>{@link #data} → 一个独立的结构化产出物（新 artifactId，完整快照）。</li>
 *   <li>{@link #tool} → 工具观察：WORKING 状态消息中的 DataPart（tools/v2）。</li>
 *   <li>{@link #progress} → 过程说明：WORKING 状态消息中的文本。</li>
 *   <li>{@link #complete} / {@link #fail} → 显式终态（COMPLETED / FAILED）后结束流。</li>
 * </ul>
 *
 * <p>线程：可以在业务线程之外调用（例如大模型回调线程），SDK 内部串行化；同一时刻只应有一个线程写。
 * 终态之后再调用任何输出方法都会抛 {@link IllegalStateException}。
 */
public interface BcnStream {

    /**
     * 仅引擎模式：用引擎的 Task 快照作为首帧。必须在任何输出之前调用，最多一次。
     * 续交和 SubscribeToTask 必须先调用它；第二档不需要调用，SDK 会自动生成骨架。
     */
    void begin(Task task);

    /** 追加一段正文文本。空字符串忽略。 */
    void text(String delta);

    /**
     * 输出一个结构化产出物（完整快照，不追加）。
     *
     * @param name 产出物名称，可为 null
     * @param data 业务 JSON 对象（Map / List / 基本类型）；长整数和金额请用字符串，线上是 protobuf Struct（double）
     */
    void data(String name, Object data);

    /**
     * 输出一条工具观察记录。调用方没有激活工具观察扩展时，SDK 直接丢弃（过程信息允许丢失，不报错）；
     * 记录不合规时也只丢弃并记日志，不影响正文。
     */
    void tool(ToolEvent event);

    /** 输出一段过程说明（例如“正在查询库存”）。属于过程信息，调用方可能不展示。 */
    void progress(String text);

    /**
     * 仅引擎模式：发出任意状态，例如 INPUT_REQUIRED（人机交互，需要 hitl/v1 激活）。第二档调用会抛异常。
     * TODO(hitl)：hitl/v1 交付时改为专门的方法，由 SDK 生成交互批次，避免业务手写状态消息。
     */
    void status(TaskStatus status);

    /** 正常完成：发出 COMPLETED 终态并结束流。 */
    void complete();

    /**
     * 失败：发出 FAILED 终态并结束流。
     *
     * @param reason 给调用方看的原因，会放进状态消息，不得包含敏感信息
     */
    void fail(String reason);

    /** 调用方是否已经取消或超时。为 true 时继续输出会被丢弃，业务应尽快返回。 */
    boolean isCancelled();
}
