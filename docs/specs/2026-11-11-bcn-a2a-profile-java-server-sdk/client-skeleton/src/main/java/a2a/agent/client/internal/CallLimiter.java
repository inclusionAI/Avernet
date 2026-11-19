package a2a.agent.client.internal;

import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicBoolean;

import a2a.agent.client.A2aCallException;

/**
 * 进行中调用数的上限（含流）。满了直接失败，不排队：调用方在高峰期快速失败，好过线程和内存被拖垮。
 */
public final class CallLimiter {

    private static final Runnable NOOP = () -> { };

    private final Semaphore permits;

    /** @param maxInFlight 0 表示不限制 */
    public CallLimiter(int maxInFlight) {
        this.permits = maxInFlight > 0 ? new Semaphore(maxInFlight) : null;
    }

    /** @return 释放许可的回调，可重复调用（只生效一次） */
    public Runnable acquire() {
        if (permits == null) {
            return NOOP;
        }
        if (!permits.tryAcquire()) {
            throw new A2aCallException(A2aCallException.Kind.BUSY, "CLIENT_BUSY", null, "too many in-flight calls", null);
        }
        AtomicBoolean released = new AtomicBoolean();
        return () -> {
            if (released.compareAndSet(false, true)) {
                permits.release();
            }
        };
    }
}
