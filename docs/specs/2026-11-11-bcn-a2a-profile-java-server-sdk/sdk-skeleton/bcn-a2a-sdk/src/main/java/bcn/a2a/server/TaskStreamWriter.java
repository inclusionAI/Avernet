package bcn.a2a.server;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import bcn.a2a.ext.BcnKeys;
import bcn.a2a.ext.ToolObservationCodec;
import bcn.a2a.spi.BcnStream;
import bcn.a2a.spi.Messages;
import bcn.a2a.spi.ToolEvent;
import org.a2aproject.sdk.spec.Artifact;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.Part;
import org.a2aproject.sdk.spec.StreamingEventKind;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskArtifactUpdateEvent;
import org.a2aproject.sdk.spec.TaskState;
import org.a2aproject.sdk.spec.TaskStatus;
import org.a2aproject.sdk.spec.TaskStatusUpdateEvent;
import org.a2aproject.sdk.spec.TextPart;

/**
 * {@link BcnStream} 的公共实现：把业务的输出调用转成标准 A2A 事件，并维护流的状态机。
 * 子类只决定事件去哪里：{@link StreamBridge} 发到网络，{@link CollectingStream} 汇总成最终 Task。
 *
 * <p>状态机保证：首帧一定是 Task；终态（或中断态）之后不再有任何事件；终态必须显式发出（P-64）。
 * 所有方法加锁串行化，业务可以从其他线程调用。
 */
abstract class TaskStreamWriter implements BcnStream {

    private final Set<String> activated;
    private final CancelSignal cancel;
    /** 引擎模式：Task ID 由引擎决定，必须先调用 begin；其他档位禁止调用 begin 和 status。 */
    private final boolean engineMode;

    private String taskId;
    private String contextId;
    private boolean started;
    private boolean closed;
    private TaskState lastState;
    private String textArtifactId;

    TaskStreamWriter(String contextId, Set<String> activated, CancelSignal cancel, boolean engineMode) {
        this.contextId = contextId;
        this.activated = activated;
        this.cancel = cancel;
        this.engineMode = engineMode;
    }

    /** 发出一个事件。调用时已持有锁。 */
    protected abstract void emit(StreamingEventKind event);

    /** 流已经结束（终态、中断态或订阅提前结束）。调用时已持有锁，只会调用一次。 */
    protected abstract void onClosed();

    // ------------------------------------------------------------------ BcnStream

    @Override
    public synchronized void begin(Task task) {
        checkOpen();
        if (!engineMode) {
            throw new IllegalStateException("begin() is only available to TaskEngine agents");
        }
        if (started) {
            throw new IllegalStateException("begin() must be called before any other output");
        }
        // TODO(实现)：校验快照：id、contextId 非空；续交时 contextId 与请求一致；artifacts / history 的大小上限。
        taskId = task.id();
        contextId = task.contextId();
        started = true;
        lastState = task.status().state();
        emit(task);
        if (endsStream(lastState)) {
            close();
        }
    }

    @Override
    public synchronized void text(String delta) {
        if (delta == null || delta.isEmpty()) {
            return;
        }
        ensureStarted();
        boolean append = textArtifactId != null;
        if (!append) {
            textArtifactId = UUID.randomUUID().toString();
        }
        Artifact artifact = Artifact.builder().artifactId(textArtifactId)
                .parts(List.<Part<?>>of(new TextPart(delta))).build();
        // TODO(实现)：正文产出物的 lastChunk。SDK 无法预知哪一段是最后一段：
        //   可以缓冲一段（发送上一段，complete 时给最后一段标 lastChunk=true），代价是延迟一段输出。待定。
        emit(TaskArtifactUpdateEvent.builder().taskId(taskId).contextId(contextId)
                .artifact(artifact).append(append).lastChunk(false).build());
    }

    @Override
    public synchronized void data(String name, Object data) {
        ensureStarted();
        // TODO(实现)：data 必须是 JSON 兼容的对象；大小上限；长整数提示（Struct 中是 double）。
        Artifact artifact = Artifact.builder().artifactId(UUID.randomUUID().toString()).name(name)
                .parts(List.<Part<?>>of(new DataPart(data))).build();
        emit(TaskArtifactUpdateEvent.builder().taskId(taskId).contextId(contextId)
                .artifact(artifact).append(false).lastChunk(true).build());
    }

    @Override
    public synchronized void tool(ToolEvent event) {
        checkOpen();
        if (!activated.contains(BcnKeys.EXT_TOOLS_V2)) {
            return; // 调用方没有激活工具观察：过程信息允许丢失，直接丢弃
        }
        ensureStarted();
        TaskStatusUpdateEvent update = ToolObservationCodec.encode(event, taskId, contextId);
        if (update == null) {
            // TODO(实现)：记 warn 日志（带 requestId），并计数，便于业务发现自己的工具记录不合规。
            return;
        }
        emit(update);
    }

    @Override
    public synchronized void progress(String text) {
        if (text == null || text.isBlank()) {
            return;
        }
        ensureStarted();
        emitStatus(TaskState.TASK_STATE_WORKING, text);
    }

    @Override
    public synchronized void status(TaskStatus status) {
        checkOpen();
        if (!engineMode) {
            throw new IllegalStateException("status() is only available to TaskEngine agents");
        }
        switch (status.state()) {
            case TASK_STATE_INPUT_REQUIRED, TASK_STATE_AUTH_REQUIRED, TASK_STATE_UNSPECIFIED ->
                    throw new IllegalStateException("state not allowed in this phase: " + status.state());
            default -> {
            }
        }
        ensureStarted();
        lastState = status.state();
        emit(TaskStatusUpdateEvent.builder().taskId(taskId).contextId(contextId).status(status).build());
        if (endsStream(lastState)) {
            close();
        }
    }

    @Override
    public synchronized void complete() {
        finish(TaskState.TASK_STATE_COMPLETED, null);
    }

    @Override
    public synchronized void fail(String reason) {
        finish(TaskState.TASK_STATE_FAILED, reason);
    }

    @Override
    public boolean isCancelled() {
        return cancel.isCancelled();
    }

    // ------------------------------------------------------------------ 供 RequestPipeline 使用

    /** 业务方法正常返回后调用：还没有终态就按完成处理。 */
    synchronized void finishIfOpen() {
        if (!closed) {
            complete();
        }
    }

    /** 订阅结束后调用：不伪造终态，只关闭流。 */
    synchronized void closeIfOpen() {
        // TODO(实现)：引擎在终态或中断态之前就返回，属于协议违规，记 warn。
        if (!closed) {
            close();
        }
    }

    /**
     * 业务方法抛出异常后调用。
     *
     * @return Task 已经发出时返回 true，并已发出 FAILED 终态；还没有发出任何事件时返回 false，由调用方返回错误
     */
    synchronized boolean abort() {
        if (!started) {
            return false;
        }
        if (!closed) {
            finish(TaskState.TASK_STATE_FAILED, "internal error");
        }
        return true;
    }

    /** 流结束时的最后状态；还没有任何事件时为 null。 */
    synchronized TaskState lastState() {
        return lastState;
    }

    // ------------------------------------------------------------------ 内部

    private void finish(TaskState state, String reason) {
        ensureStarted();
        emitStatus(state, reason);
        close();
    }

    private void emitStatus(TaskState state, String text) {
        Message message = null;
        if (text != null) {
            message = Message.builder(Messages.agentText(text)).taskId(taskId).contextId(contextId).build();
        }
        lastState = state;
        TaskStatus status = new TaskStatus(state, message, OffsetDateTime.now(ZoneOffset.UTC));
        emit(TaskStatusUpdateEvent.builder().taskId(taskId).contextId(contextId).status(status).build());
    }

    /** 第一次输出前自动发出 Task 骨架（第二档）；引擎模式必须先调用 begin。 */
    private void ensureStarted() {
        checkOpen();
        if (started) {
            return;
        }
        if (engineMode) {
            throw new IllegalStateException("TaskEngine agents must call begin() with the engine's task first");
        }
        taskId = UUID.randomUUID().toString();
        started = true;
        lastState = TaskState.TASK_STATE_WORKING;
        emit(Task.builder().id(taskId).contextId(contextId)
                .status(new TaskStatus(TaskState.TASK_STATE_WORKING, null, OffsetDateTime.now(ZoneOffset.UTC)))
                .build());
    }

    private void checkOpen() {
        if (closed) {
            throw new IllegalStateException("stream already finished");
        }
    }

    private void close() {
        closed = true;
        onClosed();
    }

    /** 终态和中断态都会结束当前的流（A2A 规定）。 */
    private static boolean endsStream(TaskState state) {
        return switch (state) {
            case TASK_STATE_COMPLETED, TASK_STATE_FAILED, TASK_STATE_CANCELED, TASK_STATE_REJECTED,
                 TASK_STATE_INPUT_REQUIRED, TASK_STATE_AUTH_REQUIRED -> true;
            default -> false;
        };
    }
}
