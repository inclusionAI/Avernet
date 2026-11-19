package a2a.agent.client.internal;

import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 把一条流上的所有回调串行化：同一时刻最多一个在执行，按提交顺序执行。
 *
 * <p>executor 为 null 时在提交任务的线程上直接执行（若已有线程在执行，则排队由它顺带执行）；
 * 否则在 executor 上执行。不为每条流创建线程。
 */
final class SerialRunner {

    private final Executor executor;
    private final Queue<Runnable> queue = new ConcurrentLinkedQueue<>();
    private final AtomicBoolean running = new AtomicBoolean();

    SerialRunner(Executor executor) {
        this.executor = executor;
    }

    void execute(Runnable task) {
        queue.add(task);
        if (running.compareAndSet(false, true)) {
            if (executor == null) {
                drain();
            } else {
                executor.execute(this::drain);
            }
        }
    }

    private void drain() {
        do {
            Runnable task;
            while ((task = queue.poll()) != null) {
                try {
                    task.run();
                } catch (RuntimeException ignored) {
                    // 任务自己负责处理异常；这里只保证一个任务失败不影响后续任务
                }
            }
            running.set(false);
        } while (!queue.isEmpty() && running.compareAndSet(false, true));
    }
}
