package a2a.agent.client.internal;

/** 对方的响应不符合 Payload 规范。reason 用于诊断，例如 INITIAL_TASK_REQUIRED、INTERMEDIATE_REPLACEMENT。 */
public final class ProtocolViolation extends RuntimeException {

    private final String reason;

    public ProtocolViolation(String reason) {
        super(reason);
        this.reason = reason;
    }

    public String reason() {
        return reason;
    }
}
