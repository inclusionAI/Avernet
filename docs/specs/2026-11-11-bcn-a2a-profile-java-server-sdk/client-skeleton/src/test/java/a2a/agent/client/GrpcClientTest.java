package a2a.agent.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import a2a.agent.client.internal.PayloadKeys;
import a2a.agent.client.transport.grpc.DirectGrpcTransport;
import com.google.protobuf.Any;
import com.google.protobuf.Struct;
import com.google.protobuf.Value;
import com.google.rpc.ErrorInfo;
import io.grpc.Metadata;
import io.grpc.Server;
import io.grpc.ServerInterceptor;
import io.grpc.ServerInterceptors;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.inprocess.InProcessChannelBuilder;
import io.grpc.inprocess.InProcessServerBuilder;
import io.grpc.protobuf.StatusProto;
import io.grpc.stub.StreamObserver;
import org.a2aproject.sdk.grpc.A2AServiceGrpc;
import org.a2aproject.sdk.grpc.Artifact;
import org.a2aproject.sdk.grpc.GetTaskRequest;
import org.a2aproject.sdk.grpc.Message;
import org.a2aproject.sdk.grpc.Part;
import org.a2aproject.sdk.grpc.Role;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.Task;
import org.a2aproject.sdk.grpc.TaskArtifactUpdateEvent;
import org.a2aproject.sdk.grpc.TaskState;
import org.a2aproject.sdk.grpc.TaskStatus;
import org.a2aproject.sdk.grpc.TaskStatusUpdateEvent;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** 用 in-process gRPC 模拟一个按 Payload 规范输出的 Agent，验证客户端的主流程。 */
class GrpcClientTest {

    private Server server;
    private A2aAgentClient client;
    private RemoteAgent agent;
    private final AtomicReference<String> lastExtensionsHeader = new AtomicReference<>();
    private final AtomicInteger getTaskCalls = new AtomicInteger();

    @BeforeEach
    void setUp() throws Exception {
        start(ClientOptions.defaults());
    }

    private void start(ClientOptions options) throws Exception {
        String name = InProcessServerBuilder.generateName();
        Metadata.Key<String> extensions = Metadata.Key.of("a2a-extensions", Metadata.ASCII_STRING_MARSHALLER);
        ServerInterceptor capture = new ServerInterceptor() {
            @Override
            public <Q, R> io.grpc.ServerCall.Listener<Q> interceptCall(io.grpc.ServerCall<Q, R> call, Metadata headers,
                                                                       io.grpc.ServerCallHandler<Q, R> next) {
                lastExtensionsHeader.set(headers.get(extensions));
                return next.startCall(call, headers);
            }
        };
        server = InProcessServerBuilder.forName(name).directExecutor()
                .addService(ServerInterceptors.intercept(new FakeAgent(), capture)).build().start();
        client = A2aAgentClient.builder()
                .transport(DirectGrpcTransport.create(url -> InProcessChannelBuilder.forName(name).directExecutor().build()))
                .callerContext(() -> CallerContext.ofUser("u-1"))
                .options(options)
                .build();
        agent = client.agent("""
                {"name":"order","description":"d","version":"1",
                 "supportedInterfaces":[{"url":"inproc://%s","protocolBinding":"GRPC","tenant":"order-agent","protocolVersion":"1.0"}],
                 "capabilities":{"streaming":true,"extensions":[{"uri":"%s","required":false}]},
                 "defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"skills":[]}
                """.formatted(name, PayloadKeys.TOOLCALL_EXTENSION));
    }

    @AfterEach
    void tearDown() {
        client.close();
        server.shutdownNow();
    }

    @Test
    void sendCarriesTenantAndCallerAndReturnsMessage() {
        Reply reply = agent.send("订单 1001");
        assertEquals(Reply.State.MESSAGE, reply.state());
        assertEquals("order-agent|u-1|订单 1001", reply.text());
        assertEquals("ctx-1", reply.contextId());
    }

    @Test
    void streamProjectsTextToolsAndTerminal() throws Exception {
        List<String> deltas = new ArrayList<>();
        List<ToolRecord> tools = new ArrayList<>();
        StreamHandle handle = agent.stream("写一份杭州两日游", new StreamListener() {
            @Override
            public void onText(String delta) {
                deltas.add(delta);
            }

            @Override
            public void onTool(ToolRecord record) {
                tools.add(record);
            }
        });
        Reply reply = handle.completion().get(5, TimeUnit.SECONDS);
        assertEquals(Reply.State.COMPLETED, reply.state());
        assertEquals(List.of("杭州两日游：", "第一天西湖，", "第二天灵隐寺。"), deltas);
        assertEquals(String.join("", deltas), reply.text());
        assertEquals(1, tools.size());
        assertEquals("search_poi", tools.get(0).name());
        assertEquals(PayloadKeys.TOOLCALL_EXTENSION, lastExtensionsHeader.get());
    }

    @Test
    void toolRecordsAreNotRequestedWhenDisabled() throws Exception {
        List<ToolRecord> tools = new ArrayList<>();
        StreamHandle handle = agent.stream(AgentRequest.text("写一份杭州两日游").toolCalls(false).build(), new StreamListener() {
            @Override
            public void onTool(ToolRecord record) {
                tools.add(record);
            }
        });
        handle.completion().get(5, TimeUnit.SECONDS);
        assertTrue(tools.isEmpty());
        assertNull(lastExtensionsHeader.get());
    }

    @Test
    void rejectionKeepsReasonAndIsDefinitelyNotExecuted() {
        A2aCallException e = assertThrows(A2aCallException.class, () -> agent.send("reject"));
        assertEquals(A2aCallException.Kind.REJECTED, e.kind());
        assertEquals("INVALID_INPUT", e.reason());
        assertTrue(e.definitelyNotExecuted());
    }

    @Test
    void streamEndingWithoutTerminalTriesGetTaskOnceThenFails() {
        StreamHandle handle = agent.stream("break", new StreamListener() { });
        ExecutionException e = assertThrows(ExecutionException.class, () -> handle.completion().get(5, TimeUnit.SECONDS));
        A2aCallException cause = (A2aCallException) e.getCause();
        assertEquals(A2aCallException.Kind.UNKNOWN_OUTCOME, cause.kind());
        assertEquals("STREAM_ENDED", cause.reason());
        assertEquals(1, getTaskCalls.get());
    }

    @Test
    void inFlightLimitRejectsWithBusyAndReleasesAfterCancel() throws Exception {
        tearDown();
        start(ClientOptions.defaults().withMaxInFlightCalls(1));
        StreamHandle hanging = agent.stream("hang", new StreamListener() { });
        CompletionException busy = assertThrows(CompletionException.class, () -> agent.sendAsync(AgentRequest.text("hi").build()).join());
        assertEquals(A2aCallException.Kind.BUSY, ((A2aCallException) busy.getCause()).kind());
        hanging.cancel();
        assertEquals(Reply.State.MESSAGE, agent.send("hi").state());
    }

    // ------------------------------------------------------------------ 模拟 Agent

    private final class FakeAgent extends A2AServiceGrpc.A2AServiceImplBase {

        @Override
        public void sendMessage(SendMessageRequest request, StreamObserver<SendMessageResponse> out) {
            String text = request.getMessage().getParts(0).getText();
            if (text.equals("reject")) {
                out.onError(error(Status.Code.INVALID_ARGUMENT, "INVALID_INPUT"));
                return;
            }
            String user = request.getMetadata().getFieldsMap().get(PayloadKeys.CALLER)
                    .getStructValue().getFieldsMap().get("userId").getStringValue();
            out.onNext(SendMessageResponse.newBuilder().setMessage(Message.newBuilder().setMessageId("m-1")
                    .setRole(Role.ROLE_AGENT).setContextId("ctx-1")
                    .addParts(Part.newBuilder().setText(request.getTenant() + "|" + user + "|" + text))).build());
            out.onCompleted();
        }

        @Override
        public void sendStreamingMessage(SendMessageRequest request, StreamObserver<StreamResponse> out) {
            String text = request.getMessage().getParts(0).getText();
            out.onNext(StreamResponse.newBuilder().setTask(Task.newBuilder().setId("task-1").setContextId("ctx-1")
                    .setStatus(TaskStatus.newBuilder().setState(TaskState.TASK_STATE_WORKING))).build());
            if (text.equals("hang")) {
                return; // 不结束，模拟一直进行中的流
            }
            out.onNext(StreamResponse.newBuilder().setStatusUpdate(TaskStatusUpdateEvent.newBuilder()
                    .setTaskId("task-1").setContextId("ctx-1").setStatus(TaskStatus.newBuilder()
                            .setState(TaskState.TASK_STATE_WORKING)
                            .setMessage(Message.newBuilder().setMessageId("tool-1").setRole(Role.ROLE_AGENT)
                                    .setTaskId("task-1").setContextId("ctx-1").addExtensions(PayloadKeys.TOOLCALL_EXTENSION)
                                    .addParts(Part.newBuilder().setData(Value.newBuilder().setStructValue(Struct.newBuilder()
                                            .putFields("toolCallId", Value.newBuilder().setStringValue("call-1").build())
                                            .putFields("name", Value.newBuilder().setStringValue("search_poi").build())
                                            .putFields("phase", Value.newBuilder().setStringValue("start").build())))))))
                    .build());
            out.onNext(artifact("杭州两日游：", false, false));
            if (text.equals("break")) {
                out.onCompleted(); // 没有终态就结束
                return;
            }
            out.onNext(artifact("第一天西湖，", true, false));
            out.onNext(artifact("第二天灵隐寺。", true, true));
            out.onNext(StreamResponse.newBuilder().setStatusUpdate(TaskStatusUpdateEvent.newBuilder()
                    .setTaskId("task-1").setContextId("ctx-1")
                    .setStatus(TaskStatus.newBuilder().setState(TaskState.TASK_STATE_COMPLETED))).build());
            out.onCompleted();
        }

        @Override
        public void getTask(GetTaskRequest request, StreamObserver<Task> out) {
            getTaskCalls.incrementAndGet();
            out.onError(error(Status.Code.NOT_FOUND, "TASK_NOT_FOUND"));
        }

        private StreamResponse artifact(String text, boolean append, boolean lastChunk) {
            return StreamResponse.newBuilder().setArtifactUpdate(TaskArtifactUpdateEvent.newBuilder()
                    .setTaskId("task-1").setContextId("ctx-1").setAppend(append).setLastChunk(lastChunk)
                    .setArtifact(Artifact.newBuilder().setArtifactId("answer").addParts(Part.newBuilder().setText(text)))).build();
        }

        private StatusRuntimeException error(Status.Code code, String reason) {
            return StatusProto.toStatusRuntimeException(com.google.rpc.Status.newBuilder().setCode(code.value()).setMessage(reason)
                    .addDetails(Any.pack(ErrorInfo.newBuilder().setReason(reason).setDomain("a2a-protocol.org").build())).build());
        }
    }
}
