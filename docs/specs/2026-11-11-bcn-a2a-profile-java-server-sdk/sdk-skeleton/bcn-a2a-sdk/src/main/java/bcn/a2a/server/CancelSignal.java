package bcn.a2a.server;

import java.time.Instant;

/**
 * 一次调用的取消状态：调用方取消（gRPC onCancel）或超过截止时间都视为已取消。
 * 业务通过 {@code BcnCall.isCancelled()} / {@code BcnStream.isCancelled()} 读取。
 */
final class CancelSignal {

    private final Instant deadline;
    private volatile boolean cancelled;

    CancelSignal(Instant deadline) {
        this.deadline = deadline;
    }

    void cancel() {
        cancelled = true;
    }

    boolean isCancelled() {
        return cancelled || Instant.now().isAfter(deadline);
    }

    boolean isTimedOut() {
        return !cancelled && Instant.now().isAfter(deadline);
    }

    Instant deadline() {
        return deadline;
    }
}
