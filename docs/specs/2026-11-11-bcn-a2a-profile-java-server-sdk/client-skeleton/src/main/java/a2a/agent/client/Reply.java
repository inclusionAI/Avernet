package a2a.agent.client;

import org.a2aproject.sdk.grpc.SendMessageResponse;

/**
 * 一次调用的结果。
 *
 * <p>{@link #text()} 是全部正文（流式调用时等于所有 {@code onText} 增量按顺序拼接的结果）。
 * Task 以 FAILED 结束不算异常，通过 {@link #state()} 表达，失败原因见 {@link #statusText()}。
 */
public final class Reply {

    public enum State {
        /** 对方以一条 Message 直接回复（Payload 规范形态 A）。 */
        MESSAGE,
        COMPLETED,
        FAILED,
        CANCELED,
        REJECTED,
        /** 停在中断态（INPUT_REQUIRED / AUTH_REQUIRED）。本期 Payload 规范不使用，出现时调用方自行处理。 */
        INTERRUPTED,
        /** 非流式调用拿到了未结束的 Task（对方提前返回）。只有支持查询的 Agent 才能再用 getTask 跟进。 */
        WORKING
    }

    private final State state;
    private final String text;
    private final String taskId;
    private final String contextId;
    private final String statusText;
    private final SendMessageResponse raw;

    public Reply(State state, String text, String taskId, String contextId, String statusText, SendMessageResponse raw) {
        this.state = state;
        this.text = text;
        this.taskId = taskId;
        this.contextId = contextId;
        this.statusText = statusText;
        this.raw = raw;
    }

    public State state() {
        return state;
    }

    /** MESSAGE 或 COMPLETED。 */
    public boolean isSuccess() {
        return state == State.MESSAGE || state == State.COMPLETED;
    }

    public String text() {
        return text;
    }

    /** 形态 A 的回复没有 Task，为 null。 */
    public String taskId() {
        return taskId;
    }

    /** 会话 ID，多轮对话时沿用；对方没有给出时为 null。 */
    public String contextId() {
        return contextId;
    }

    /** 最后一条状态消息的文本（例如失败原因）；没有时为 null。 */
    public String statusText() {
        return statusText;
    }

    /** 非流式调用的原始响应（protobuf），流式调用为 null。需要读取 metadata 等原始字段时使用。 */
    public SendMessageResponse raw() {
        return raw;
    }

    @Override
    public String toString() {
        return "Reply{" + state + ", taskId=" + taskId + ", contextId=" + contextId + ", text=" + text + "}";
    }
}
