package a2a.agent.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletionException;
import java.util.concurrent.TimeUnit;

import a2a.agent.client.transport.http.HttpClients;
import a2a.agent.client.transport.http.HttpJsonTransport;
import a2a.agent.client.transport.http.JsonRpcTransport;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.protobuf.MessageOrBuilder;
import com.google.protobuf.util.JsonFormat;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.a2aproject.sdk.grpc.Artifact;
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

/** 用 JDK HttpServer 模拟 HTTP+JSON 和 JSONRPC 两种绑定的 Agent。 */
class HttpTransportsTest {

    private HttpServer server;
    private A2aAgentClient client;
    private String base;

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/agents/order-agent/message:send", this::httpJsonSend);
        server.createContext("/rpc", this::jsonRpc);
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort();
        var http = HttpClients.newDefault();
        client = A2aAgentClient.builder()
                .transport(HttpJsonTransport.create(http))
                .transport(JsonRpcTransport.create(http))
                .build();
    }

    @AfterEach
    void tearDown() {
        client.close();
        server.stop(0);
    }

    @Test
    void httpJsonSendUsesTenantPathAndReturnsMessage() {
        RemoteAgent agent = client.agent(card("HTTP+JSON", base + "/agents", "order-agent"));
        Reply reply = agent.send("订单 1001");
        assertEquals(Reply.State.MESSAGE, reply.state());
        assertEquals("order-agent:订单 1001", reply.text());
    }

    @Test
    void jsonRpcStreamsOverSse() throws Exception {
        RemoteAgent agent = client.agent(card("JSONRPC", base + "/rpc", ""));
        List<String> deltas = new ArrayList<>();
        Reply reply = agent.stream("写一份杭州两日游", new StreamListener() {
            @Override
            public void onText(String delta) {
                deltas.add(delta);
            }
        }).completion().get(5, TimeUnit.SECONDS);
        assertEquals(Reply.State.COMPLETED, reply.state());
        assertEquals(List.of("杭州两日游：", "第一天西湖。"), deltas);
    }

    @Test
    void jsonRpcErrorKeepsReason() {
        RemoteAgent agent = client.agent(card("JSONRPC", base + "/rpc", ""));
        CompletionException e = assertThrows(CompletionException.class, () -> agent.getTask("task-x").join());
        A2aCallException cause = (A2aCallException) e.getCause();
        assertEquals(A2aCallException.Kind.REJECTED, cause.kind());
        assertEquals("TASK_NOT_FOUND", cause.reason());
    }

    // ------------------------------------------------------------------ 模拟 Agent

    private static String card(String binding, String url, String tenant) {
        return """
                {"name":"order","description":"d","version":"1",
                 "supportedInterfaces":[{"url":"%s","protocolBinding":"%s","tenant":"%s","protocolVersion":"1.0"}],
                 "capabilities":{"streaming":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"skills":[]}
                """.formatted(url, binding, tenant);
    }

    private void httpJsonSend(HttpExchange exchange) throws IOException {
        if (!"1.0".equals(exchange.getRequestHeaders().getFirst("A2A-Version"))) {
            reply(exchange, 400, "application/json", "{}");
            return;
        }
        SendMessageRequest.Builder request = SendMessageRequest.newBuilder();
        JsonFormat.parser().merge(body(exchange), request);
        SendMessageResponse response = SendMessageResponse.newBuilder().setMessage(Message.newBuilder().setMessageId("m-1")
                .setRole(Role.ROLE_AGENT).addParts(Part.newBuilder()
                        .setText(request.getTenant() + ":" + request.getMessage().getParts(0).getText()))).build();
        reply(exchange, 200, "application/json", print(response));
    }

    private void jsonRpc(HttpExchange exchange) throws IOException {
        JsonObject envelope = JsonParser.parseString(body(exchange)).getAsJsonObject();
        String id = envelope.get("id").toString();
        String method = envelope.get("method").getAsString();
        if (method.equals("GetTask")) {
            reply(exchange, 200, "application/json", "{\"jsonrpc\":\"2.0\",\"id\":" + id
                    + ",\"error\":{\"code\":-32001,\"message\":\"Task not found\",\"data\":{\"reason\":\"TASK_NOT_FOUND\"}}}");
            return;
        }
        exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
        exchange.sendResponseHeaders(200, 0);
        try (OutputStream out = exchange.getResponseBody()) {
            for (StreamResponse event : List.of(
                    StreamResponse.newBuilder().setTask(Task.newBuilder().setId("task-1").setContextId("ctx-1")
                            .setStatus(TaskStatus.newBuilder().setState(TaskState.TASK_STATE_WORKING))).build(),
                    artifact("杭州两日游：", false),
                    artifact("第一天西湖。", true),
                    StreamResponse.newBuilder().setStatusUpdate(TaskStatusUpdateEvent.newBuilder().setTaskId("task-1")
                            .setContextId("ctx-1").setStatus(TaskStatus.newBuilder().setState(TaskState.TASK_STATE_COMPLETED))).build())) {
                String data = "{\"jsonrpc\":\"2.0\",\"id\":" + id + ",\"result\":" + print(event) + "}";
                out.write(("data: " + data + "\n\n").getBytes(StandardCharsets.UTF_8));
                out.flush();
            }
        }
    }

    private static StreamResponse artifact(String text, boolean append) {
        return StreamResponse.newBuilder().setArtifactUpdate(TaskArtifactUpdateEvent.newBuilder().setTaskId("task-1")
                .setContextId("ctx-1").setAppend(append)
                .setArtifact(Artifact.newBuilder().setArtifactId("answer").addParts(Part.newBuilder().setText(text)))).build();
    }

    private static String print(MessageOrBuilder message) throws IOException {
        return JsonFormat.printer().omittingInsignificantWhitespace().print(message);
    }

    private static String body(HttpExchange exchange) throws IOException {
        return new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
    }

    private static void reply(HttpExchange exchange, int status, String contentType, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", contentType);
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }
}
