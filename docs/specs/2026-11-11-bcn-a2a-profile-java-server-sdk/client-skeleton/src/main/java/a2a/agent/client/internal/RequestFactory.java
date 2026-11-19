package a2a.agent.client.internal;

import java.util.LinkedHashMap;
import java.util.Map;

import a2a.agent.client.AgentRequest;
import a2a.agent.client.CallerContext;
import a2a.agent.client.ClientOptions;
import a2a.agent.client.card.SelectedInterface;
import org.a2aproject.sdk.grpc.CancelTaskRequest;
import org.a2aproject.sdk.grpc.GetTaskRequest;
import org.a2aproject.sdk.grpc.Message;
import org.a2aproject.sdk.grpc.Part;
import org.a2aproject.sdk.grpc.Role;
import org.a2aproject.sdk.grpc.SendMessageConfiguration;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SubscribeToTaskRequest;

/**
 * 按 Payload 规范构造请求。直接构造 protobuf 对象（不经过 a2a-java POJO 再转换），三种绑定通用。
 */
public final class RequestFactory {

    private static final SendMessageConfiguration TEXT_ONLY =
            SendMessageConfiguration.newBuilder().addAcceptedOutputModes("text/plain").build();

    private RequestFactory() {
    }

    /**
     * 构造 SendMessage / SendStreamingMessage 的请求。
     *
     * <ul>
     *   <li>messageId 每次新生成；role 为 USER；只有一个文本 Part（本期只发文本）；</li>
     *   <li>tenant 取自 Card 接口项；contextId、taskId、referenceTaskIds 来自请求；</li>
     *   <li>请求级 metadata = 业务字段 + {@code bcn_caller}（调用上下文由接入方提供）；</li>
     *   <li>不设置 returnImmediately：一元调用等到 Task 结束再返回（A2A 默认的阻塞语义）。</li>
     * </ul>
     */
    public static SendMessageRequest sendMessage(SelectedInterface selected, AgentRequest request, CallerContext caller) {
        Message.Builder message = Message.newBuilder()
                .setMessageId(Ids.newMessageId())
                .setRole(Role.ROLE_USER)
                .addParts(Part.newBuilder().setText(request.text()))
                .addAllReferenceTaskIds(request.referenceTaskIds());
        if (request.contextId() != null) {
            message.setContextId(request.contextId());
        }
        if (request.taskId() != null) {
            message.setTaskId(request.taskId());
        }
        SendMessageRequest.Builder builder = SendMessageRequest.newBuilder()
                .setMessage(message)
                .setConfiguration(TEXT_ONLY);
        if (!selected.tenant().isEmpty()) {
            builder.setTenant(selected.tenant());
        }
        if (!request.metadata().isEmpty() || caller != null) {
            Map<String, Object> metadata = new LinkedHashMap<>(request.metadata());
            if (caller != null) {
                metadata.put(PayloadKeys.CALLER, caller.toPayload());
            }
            builder.setMetadata(Protos.toStruct(metadata));
        }
        return builder.build();
    }

    /** 本次是否请求工具调用记录：调用方想要，且对方 Card 声明了 toolcall 扩展。 */
    public static boolean toolsEnabled(SelectedInterface selected, AgentRequest request, ClientOptions options) {
        boolean wanted = request.toolCalls() != null ? request.toolCalls() : options.requestToolCalls();
        return wanted && selected.declaresExtension(PayloadKeys.TOOLCALL_EXTENSION);
    }

    /** 请求头 A2A-Extensions 的值；不请求任何扩展时为 null。 */
    public static String extensionsHeader(boolean toolsEnabled) {
        return toolsEnabled ? PayloadKeys.TOOLCALL_EXTENSION : null;
    }

    public static GetTaskRequest getTask(SelectedInterface selected, String taskId) {
        return GetTaskRequest.newBuilder().setTenant(selected.tenant()).setId(taskId).build();
    }

    public static CancelTaskRequest cancelTask(SelectedInterface selected, String taskId) {
        return CancelTaskRequest.newBuilder().setTenant(selected.tenant()).setId(taskId).build();
    }

    public static SubscribeToTaskRequest subscribe(SelectedInterface selected, String taskId) {
        return SubscribeToTaskRequest.newBuilder().setTenant(selected.tenant()).setId(taskId).build();
    }
}
