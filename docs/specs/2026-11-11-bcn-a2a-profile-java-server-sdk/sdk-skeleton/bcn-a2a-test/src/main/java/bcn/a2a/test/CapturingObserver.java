package bcn.a2a.test;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import io.grpc.stub.StreamObserver;

/** 收集服务端输出的观察者，用于测试。线程安全；{@link #await} 等待调用结束。 */
public final class CapturingObserver<T> implements StreamObserver<T> {

    private final List<T> values = new ArrayList<>();
    private final CountDownLatch done = new CountDownLatch(1);
    private volatile Throwable error;

    @Override
    public synchronized void onNext(T value) {
        values.add(value);
    }

    @Override
    public void onError(Throwable t) {
        error = t;
        done.countDown();
    }

    @Override
    public void onCompleted() {
        done.countDown();
    }

    /** 等待调用结束；超时抛 {@link AssertionError}。 */
    public CapturingObserver<T> await(Duration timeout) {
        try {
            if (!done.await(timeout.toMillis(), TimeUnit.MILLISECONDS)) {
                throw new AssertionError("call did not finish within " + timeout);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new AssertionError("interrupted", e);
        }
        return this;
    }

    public synchronized List<T> values() {
        return List.copyOf(values);
    }

    /** 调用以错误结束时的异常（通常是 StatusRuntimeException），成功时为 null。 */
    public Throwable error() {
        return error;
    }
}
