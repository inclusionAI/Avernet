package a2a.agent.client.internal;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import a2a.agent.client.Reply;
import a2a.agent.client.ToolRecord;
import org.a2aproject.sdk.grpc.Message;
import org.a2aproject.sdk.grpc.Part;
import org.a2aproject.sdk.grpc.Role;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.Task;
import org.a2aproject.sdk.grpc.TaskArtifactUpdateEvent;
import org.a2aproject.sdk.grpc.TaskState;
import org.a2aproject.sdk.grpc.TaskStatus;
import org.a2aproject.sdk.grpc.TaskStatusUpdateEvent;

/**
 * 把响应（一元或流式）按 Payload 规范第 6 节“投影”成正文增量、工具记录、状态和最终结果。
 * 规则与 BCS 的实现一致（apps/bcs/.../bcs-a2a-http/src/projection.rs、text.rs）：
 * <ul>
 *   <li>流的首帧必须是 Task（或形态 A 的唯一一条 Message），之后 taskId / contextId 不变；</li>
 *   <li>正文按 append / lastChunk 规则拼接，只输出新增部分；中途替换已发出的内容属于违规；</li>
 *   <li>只认显式终态；终态之后再有事件属于违规（重复同一个终态除外）；</li>
 *   <li>工具记录只在本次请求了 toolcall 时解码，不合规的记录丢弃。</li>
 * </ul>
 *
 * <p><b>性能</b>：直接在 protobuf 对象上处理，不转换成 a2a-java POJO；每条流一个实例，单线程使用，不加锁。
 */
public final class ResponseProjector {

    /** 投影过程中产生的回调。在处理事件的线程上同步调用。 */
    public interface Events {
        Events NONE = new Events() { };

        default void text(String delta) {
        }

        default void tool(ToolRecord record) {
        }

        default void status(Reply.State state, String text) {
        }
    }

    private final boolean toolsEnabled;
    private final Events events;
    private final Map<String, TextState> artifacts = new HashMap<>();
    private final StringBuilder allText = new StringBuilder();

    private String taskId;
    private String contextId;
    private TaskState lastState;
    private String statusText;
    private boolean messageMode;
    private boolean finished;

    public ResponseProjector(boolean toolsEnabled, Events events) {
        this.toolsEnabled = toolsEnabled;
        this.events = events;
    }

    /** 处理流中的一个事件。违规时抛 {@link ProtocolViolation}。 */
    public void accept(StreamResponse event) {
        switch (event.getPayloadCase()) {
            case TASK -> task(event.getTask());
            case MESSAGE -> message(event.getMessage());
            case STATUS_UPDATE -> statusUpdate(event.getStatusUpdate());
            case ARTIFACT_UPDATE -> artifactUpdate(event.getArtifactUpdate());
            default -> throw new ProtocolViolation("EMPTY_STREAM_RESPONSE");
        }
    }

    /** 处理一元调用的响应（Message 或 Task 快照）。 */
    public void acceptUnary(SendMessageResponse response) {
        switch (response.getPayloadCase()) {
            case MESSAGE -> message(response.getMessage());
            case TASK -> task(response.getTask());
            default -> throw new ProtocolViolation("EMPTY_SEND_MESSAGE_RESPONSE");
        }
    }

    /** 断流后用 GetTask 拿到的快照：补齐缺失的正文，并更新状态。 */
    public void acceptSnapshot(Task task) {
        task(task);
    }

    /** 已经收到终态（或中断态），或者形态 A 的那条 Message。 */
    public boolean finished() {
        return finished;
    }

    public String taskId() {
        return taskId;
    }

    /** @param raw 一元调用的原始响应，流式调用传 null */
    public Reply reply(SendMessageResponse raw) {
        Reply.State state = messageMode ? Reply.State.MESSAGE : map(lastState);
        return new Reply(state, allText.toString(), taskId, contextId, statusText, raw);
    }

    // ------------------------------------------------------------------ 各类事件

    private void task(Task task) {
        identity(task.getId(), task.getContextId(), true);
        for (var artifact : task.getArtifactsList()) {
            emitText(artifact(artifact.getArtifactId()).snapshot(text(artifact.getPartsList())));
        }
        if (task.hasStatus()) {
            status(task.getStatus());
        }
    }

    private void message(Message message) {
        if (taskId != null) {
            throw new ProtocolViolation("UNEXPECTED_MESSAGE");
        }
        if (finished) {
            throw new ProtocolViolation("EVENT_AFTER_TERMINAL");
        }
        if (message.getRole() != Role.ROLE_AGENT) {
            throw new ProtocolViolation("INVALID_ROLE");
        }
        messageMode = true;
        contextId = message.getContextId().isEmpty() ? null : message.getContextId();
        emitText(text(message.getPartsList()));
        finished = true;
    }

    private void statusUpdate(TaskStatusUpdateEvent event) {
        identity(event.getTaskId(), event.getContextId(), false);
        status(event.getStatus());
    }

    private void artifactUpdate(TaskArtifactUpdateEvent event) {
        identity(event.getTaskId(), event.getContextId(), false);
        if (finished) {
            throw new ProtocolViolation("EVENT_AFTER_TERMINAL");
        }
        String delta = artifact(event.getArtifact().getArtifactId())
                .update(text(event.getArtifact().getPartsList()), event.getAppend(), event.getLastChunk());
        emitText(delta);
    }

    private void status(TaskStatus status) {
        TaskState state = status.getState();
        if (finished) {
            if (state == lastState) {
                return; // 重复同一个终态可以容忍
            }
            throw new ProtocolViolation("EVENT_AFTER_TERMINAL");
        }
        boolean changed = state != lastState;
        lastState = state;
        String text = null;
        if (status.hasMessage()) {
            Message message = status.getMessage();
            if (message.getExtensionsList().contains(PayloadKeys.TOOLCALL_EXTENSION)) {
                if (toolsEnabled) {
                    for (Part part : message.getPartsList()) {
                        ToolRecord record = ToolRecordDecoder.decode(part);
                        if (record != null) {
                            events.tool(record);
                        }
                        // TODO(实现)：不合规的工具记录计数并记 debug 日志
                    }
                }
            } else {
                text = text(message.getPartsList());
                if (!text.isEmpty()) {
                    statusText = text;
                }
            }
        }
        if (endsStream(state)) {
            finished = true;
        }
        if (changed || (text != null && !text.isEmpty())) {
            events.status(map(state), text == null || text.isEmpty() ? null : text);
        }
    }

    private void identity(String id, String context, boolean isTask) {
        if (messageMode) {
            throw new ProtocolViolation("EVENT_AFTER_MESSAGE");
        }
        if (taskId == null) {
            if (!isTask) {
                throw new ProtocolViolation("INITIAL_TASK_REQUIRED");
            }
            if (id.isEmpty() || context.isEmpty()) {
                throw new ProtocolViolation("IDENTITY_MISSING");
            }
            taskId = id;
            contextId = context;
        } else if (!taskId.equals(id) || !contextId.equals(context)) {
            throw new ProtocolViolation("IDENTITY_MISMATCH");
        }
    }

    private void emitText(String delta) {
        if (!delta.isEmpty()) {
            allText.append(delta);
            events.text(delta);
        }
    }

    private TextState artifact(String artifactId) {
        return artifacts.computeIfAbsent(artifactId, k -> new TextState());
    }

    /** 拼接文本 Part。本期回复只有文本；其他类型忽略（需要时从原始响应读取）。 */
    private static String text(List<Part> parts) {
        if (parts.size() == 1) {
            Part only = parts.get(0);
            return only.getContentCase() == Part.ContentCase.TEXT ? only.getText() : "";
        }
        StringBuilder sb = new StringBuilder();
        for (Part part : parts) {
            if (part.getContentCase() == Part.ContentCase.TEXT) {
                sb.append(part.getText());
            }
        }
        return sb.toString();
    }

    private static boolean endsStream(TaskState state) {
        return switch (state) {
            case TASK_STATE_COMPLETED, TASK_STATE_FAILED, TASK_STATE_CANCELED, TASK_STATE_REJECTED,
                 TASK_STATE_INPUT_REQUIRED, TASK_STATE_AUTH_REQUIRED -> true;
            default -> false;
        };
    }

    private static Reply.State map(TaskState state) {
        if (state == null) {
            return Reply.State.WORKING;
        }
        return switch (state) {
            case TASK_STATE_COMPLETED -> Reply.State.COMPLETED;
            case TASK_STATE_FAILED -> Reply.State.FAILED;
            case TASK_STATE_CANCELED -> Reply.State.CANCELED;
            case TASK_STATE_REJECTED -> Reply.State.REJECTED;
            case TASK_STATE_INPUT_REQUIRED, TASK_STATE_AUTH_REQUIRED -> Reply.State.INTERRUPTED;
            default -> Reply.State.WORKING;
        };
    }

    /** 一个文本产出物的拼接状态，规则同 BCS 的 text.rs。 */
    private static final class TextState {
        private final StringBuilder text = new StringBuilder();
        private boolean initialized;
        private boolean lastChunkSeen;

        String update(String chunk, boolean append, boolean lastChunk) {
            if (lastChunkSeen) {
                if (!append && lastChunk && text.toString().equals(chunk)) {
                    return ""; // 恢复过程中可能再次看到相同的完整内容
                }
                throw new ProtocolViolation("ARTIFACT_ALREADY_FINISHED");
            }
            String suffix;
            if (append) {
                if (!initialized) {
                    throw new ProtocolViolation("MISSING_BASELINE");
                }
                suffix = chunk;
            } else if (!initialized) {
                suffix = chunk;
            } else {
                if (!lastChunk) {
                    throw new ProtocolViolation("INTERMEDIATE_REPLACEMENT");
                }
                suffix = suffixAfterSent(chunk);
            }
            text.append(suffix);
            initialized = true;
            lastChunkSeen = lastChunk;
            return suffix;
        }

        String snapshot(String full) {
            String suffix = initialized ? suffixAfterSent(full) : full;
            if (lastChunkSeen && !suffix.isEmpty()) {
                throw new ProtocolViolation("ARTIFACT_ALREADY_FINISHED");
            }
            text.append(suffix);
            initialized = true;
            return suffix;
        }

        private String suffixAfterSent(String full) {
            String sent = text.toString();
            if (!full.startsWith(sent)) {
                throw new ProtocolViolation("REVISION_UNSUPPORTED");
            }
            return full.substring(sent.length());
        }
    }
}
