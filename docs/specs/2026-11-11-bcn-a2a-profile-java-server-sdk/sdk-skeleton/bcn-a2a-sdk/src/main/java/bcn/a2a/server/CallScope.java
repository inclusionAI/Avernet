package bcn.a2a.server;

import java.util.concurrent.atomic.AtomicBoolean;

import bcn.a2a.spi.A2aListener;
import bcn.a2a.spi.BcnException;
import io.grpc.stub.StreamObserver;
import org.a2aproject.sdk.spec.A2AError;
import org.a2aproject.sdk.spec.InternalError;
import org.a2aproject.sdk.spec.InvalidAgentResponseError;
import org.a2aproject.sdk.spec.TaskState;

/**
 * 一次调用的上下文：监听回调、取消状态，以及“恰好结束一次”的保证。
 */
final class CallScope {

    final A2aListener.CallInfo info;
    final CancelSignal cancel;
    private final A2aListener listener;
    private final AtomicBoolean ended = new AtomicBoolean();

    CallScope(A2aListener.CallInfo info, CancelSignal cancel, A2aListener listener) {
        this.info = info;
        this.cancel = cancel;
        this.listener = listener;
    }

    /** 记录调用结束；多次调用只有第一次生效。 */
    void end(A2aListener.Outcome outcome, Throwable error) {
        if (ended.compareAndSet(false, true)) {
            try {
                listener.onEnd(info, outcome, error);
            } catch (RuntimeException ignored) {
                // TODO(实现)：记日志。监听器的异常不能影响调用。
            }
        }
    }

    /** 以错误结束调用：映射错误码、通知调用方、记录监听。 */
    <T> void fail(StreamObserver<T> out, Throwable error) {
        end(outcomeOf(error), error);
        try {
            out.onError(ErrorMapper.toStatus(error));
        } catch (RuntimeException ignored) {
            // 调用方已经取消，或者这条调用已经结束
        }
    }

    /** 并发已满或线程池拒绝。 */
    <T> void busy(StreamObserver<T> out) {
        end(A2aListener.Outcome.BUSY, null);
        try {
            out.onError(ErrorMapper.busy());
        } catch (RuntimeException ignored) {
            // 同上
        }
    }

    /** 流式档位按最后状态归类。 */
    A2aListener.Outcome outcomeOf(TaskState lastState) {
        if (lastState == TaskState.TASK_STATE_COMPLETED) {
            return A2aListener.Outcome.OK;
        }
        if (cancel.isTimedOut()) {
            return A2aListener.Outcome.TIMEOUT;
        }
        if (cancel.isCancelled() || lastState == TaskState.TASK_STATE_CANCELED) {
            return A2aListener.Outcome.CANCELLED;
        }
        return A2aListener.Outcome.FAILED;
    }

    private A2aListener.Outcome outcomeOf(Throwable error) {
        if (cancel.isTimedOut()) {
            return A2aListener.Outcome.TIMEOUT;
        }
        if (cancel.isCancelled()) {
            return A2aListener.Outcome.CANCELLED;
        }
        if (error instanceof BcnException
                || (error instanceof A2AError && !(error instanceof InternalError) && !(error instanceof InvalidAgentResponseError))) {
            return A2aListener.Outcome.REJECTED;
        }
        return A2aListener.Outcome.FAILED;
    }
}
