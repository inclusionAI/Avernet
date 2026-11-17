package bcn.a2a.ext;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import bcn.a2a.spi.ToolEvent;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.Part;
import org.a2aproject.sdk.spec.TaskState;
import org.a2aproject.sdk.spec.TaskStatus;
import org.a2aproject.sdk.spec.TaskStatusUpdateEvent;

/**
 * 工具观察扩展（tools/v2）的编码：一条工具记录 = 一个 WORKING 状态更新，
 * 状态消息只含一个 DataPart，消息的 extensions 标记 tools/v2 的 URI。
 */
public final class ToolObservationCodec {

    private ToolObservationCodec() {
    }

    /**
     * 把工具记录编码为状态更新事件。
     *
     * @return 记录不合规时返回 null，调用方丢弃这一帧并记日志（工具观察允许丢失，不能因此中断正文）
     */
    public static TaskStatusUpdateEvent encode(ToolEvent event, String taskId, String contextId) {
        if (event == null || isBlank(event.toolCallId()) || isBlank(event.name()) || event.phase() == null) {
            return null;
        }
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("toolCallId", event.toolCallId());
        data.put("name", event.name());
        data.put("phase", event.phase().name().toLowerCase(Locale.ROOT));
        if (event.args() != null) {
            data.put("args", event.args());
        }
        if (event.result() != null) {
            data.put("result", event.result());
        }
        if (event.isError() != null) {
            data.put("isError", event.isError());
        }
        // TODO(实现)：
        //  1. args / result 的大小上限：超出时截断并带截断标记，不判为不合规；
        //  2. 长整数转字符串（Struct 的数字是 double，超过 2^53 会丢精度）；
        //  3. 来源类附加信息（serverId 等）放 Part.metadata 的业务键，不进 data。
        Message message = Message.builder()
                .role(Message.Role.ROLE_AGENT)
                .taskId(taskId)
                .contextId(contextId)
                .parts(List.<Part<?>>of(new DataPart(data)))
                .extensions(List.of(BcnKeys.EXT_TOOLS_V2))
                .build();
        TaskStatus status = new TaskStatus(TaskState.TASK_STATE_WORKING, message, OffsetDateTime.now(ZoneOffset.UTC));
        return TaskStatusUpdateEvent.builder().taskId(taskId).contextId(contextId).status(status).build();
    }

    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }
}
