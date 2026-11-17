package bcn.a2a.test;

import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Executor;

import bcn.a2a.server.A2aService;
import bcn.a2a.spi.BcnAgent;
import bcn.a2a.spi.Messages;
import io.grpc.Status;
import org.a2aproject.sdk.grpc.Message;
import org.a2aproject.sdk.grpc.Part;
import org.a2aproject.sdk.grpc.Role;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.utils.ProtoUtils;
import org.a2aproject.sdk.spec.Artifact;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TextPart;

/**
 * 业务单元测试用的运行器：不启动端口，但请求会经过完整的 SDK 流水线（分发、校验、出站检查、错误映射）。
 *
 * <pre>{@code
 * var tester = A2aAgentTester.of(new OrderAgent(orderService));
 * assertEquals("订单 1001 已发货", tester.send("查一下订单 1001").text());
 * }</pre>
 *
 * <p>TODO(实现)：
 * <ul>
 *   <li>改为 in-process gRPC 通道，覆盖请求头（A2A-Extensions / A2A-Version）和协商结果，
 *       目前直接调用服务对象，不经过拦截器，因此所有扩展都视为未请求；</li>
 *   <li>提供请求构造器：带 contextId、taskId、业务 metadata、扩展请求（工具观察）；</li>
 *   <li>TCK：在业务测试类上加一个注解，就在进程内对自己的 Agent 跑一遍一致性用例。</li>
 * </ul>
 */
public final class A2aAgentTester {

    private static final Duration TIMEOUT = Duration.ofSeconds(30);

    private final A2aService service;

    private A2aAgentTester(A2aService service) {
        this.service = service;
    }

    /** 用调用线程直接执行业务代码，测试是同步的。 */
    public static A2aAgentTester of(BcnAgent... agents) {
        return of(Runnable::run, agents);
    }

    public static A2aAgentTester of(Executor executor, BcnAgent... agents) {
        return new A2aAgentTester(A2aService.builder().agents(List.of(agents)).executor(executor).build());
    }

    /** SendMessage 给应用中唯一的 Agent。 */
    public Reply send(String text) {
        return send("", text);
    }

    /** SendMessage 给指定 Agent。 */
    public Reply send(String agentId, String text) {
        CapturingObserver<SendMessageResponse> observer = new CapturingObserver<>();
        service.sendMessage(request(agentId, text), observer);
        observer.await(TIMEOUT);
        if (observer.error() != null) {
            return new Reply(null, observer.error());
        }
        return new Reply(observer.values().get(0), null);
    }

    /** SendStreamingMessage，返回流中的全部事件（a2a-java 模型）。调用以错误结束时抛出该错误。 */
    public List<org.a2aproject.sdk.spec.StreamingEventKind> stream(String agentId, String text) {
        CapturingObserver<StreamResponse> observer = new CapturingObserver<>();
        service.sendStreamingMessage(request(agentId, text), observer);
        observer.await(TIMEOUT);
        if (observer.error() != null) {
            throw new AssertionError("stream failed: " + Status.fromThrowable(observer.error()), observer.error());
        }
        return observer.values().stream().map(ProtoUtils.FromProto::streamingEventKind).toList();
    }

    private static SendMessageRequest request(String tenant, String text) {
        Message message = Message.newBuilder()
                .setMessageId(UUID.randomUUID().toString())
                .setRole(Role.ROLE_USER)
                .addParts(Part.newBuilder().setText(text))
                .build();
        return SendMessageRequest.newBuilder().setTenant(tenant).setMessage(message).build();
    }

    /** SendMessage 的结果：成功时是 Message 或 Task，失败时是 gRPC 错误。 */
    public record Reply(SendMessageResponse response, Throwable error) {

        /** 失败时的 gRPC 状态码；成功时为 OK。 */
        public Status.Code code() {
            return error == null ? Status.Code.OK : Status.fromThrowable(error).getCode();
        }

        /** 回复的全部文本：Message 取文本 Part；Task 取各产出物中的文本 Part。 */
        public String text() {
            if (error != null) {
                throw new AssertionError("call failed: " + Status.fromThrowable(error), error);
            }
            if (response.hasMessage()) {
                return Messages.textOf(ProtoUtils.FromProto.message(response.getMessage()));
            }
            Task task = ProtoUtils.FromProto.task(response.getTask());
            StringBuilder text = new StringBuilder();
            for (Artifact artifact : task.artifacts()) {
                artifact.parts().stream().filter(TextPart.class::isInstance)
                        .forEach(p -> text.append(((TextPart) p).text()));
            }
            return text.toString();
        }
    }
}
