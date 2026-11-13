package io.avernet.undercover.player.bcn;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.avernet.undercover.player.config.BcnProperties;
import io.avernet.undercover.player.run.RunRegistry;
import io.avernet.undercover.player.support.FakeWebSocket;
import io.avernet.undercover.player.support.RecordingFrameSender;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class BcnWebSocketClientTest {

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void handshakeIsFirstFrameAndHeartbeatFollows(@TempDir Path tempDir) throws Exception {
        BcnProperties properties = properties(tempDir);
        AtomicReference<FakeWebSocket> socketRef = new AtomicReference<>();
        WebSocketConnector connector = (uri, listener, timeout) -> {
            FakeWebSocket socket = new FakeWebSocket(listener);
            socket.setOnSend(message -> {
                JsonNode frame = read(message);
                if ("bot.connect".equals(frame.path("method").asText())) {
                    socket.emit(connectResponse(frame.path("id").asText(), "tok-1", "bot-1").toString());
                }
            });
            socketRef.set(socket);
            return CompletableFuture.completedFuture(socket);
        };
        BcnWebSocketClient client = client(properties, connector);

        try {
            client.start();
            await(() -> socketRef.get() != null);
            FakeWebSocket socket = socketRef.get();
            await(() -> !socket.sent().isEmpty());
            JsonNode connect = read(socket.sent().get(0));
            assertThat(connect.path("method").asText()).isEqualTo("bot.connect");
            assertThat(connect.path("params").path("protocol_version").asInt()).isEqualTo(3);
            assertThat(connect.path("params").path("client_kind").asText()).isEqualTo("custom-engine");

            await(() -> Files.exists(properties.getSessionFile()));
            await(() -> socket.sent().stream()
                    .map(this::read)
                    .anyMatch(frame -> "bot.status".equals(frame.path("method").asText())));
        } finally {
            client.stop();
        }
    }

    @Test
    void savedTokenAndBotIdAreReused(@TempDir Path tempDir) throws Exception {
        BcnProperties properties = properties(tempDir);
        SessionStore sessions = new SessionStore(mapper, properties);
        sessions.save("saved-token", "saved-bot");
        AtomicReference<FakeWebSocket> socketRef = new AtomicReference<>();
        WebSocketConnector connector = (uri, listener, timeout) -> {
            FakeWebSocket socket = new FakeWebSocket(listener);
            socket.setOnSend(message -> {
                JsonNode frame = read(message);
                if ("bot.connect".equals(frame.path("method").asText())) {
                    socket.emit(connectResponse(frame.path("id").asText(), "saved-token", "saved-bot").toString());
                }
            });
            socketRef.set(socket);
            return CompletableFuture.completedFuture(socket);
        };
        BcnWebSocketClient client = client(properties, connector, sessions);

        try {
            client.start();
            await(() -> socketRef.get() != null);
            FakeWebSocket socket = socketRef.get();
            await(() -> !socket.sent().isEmpty());
            JsonNode params = read(socket.sent().get(0)).path("params");
            assertThat(params.path("token").asText()).isEqualTo("saved-token");
            assertThat(params.path("bot_id").asText()).isEqualTo("saved-bot");
        } finally {
            client.stop();
        }
    }

    @Test
    void alreadyConnectedResponseIsRetriedWithBackoff(@TempDir Path tempDir) throws Exception {
        BcnProperties properties = properties(tempDir);
        properties.setReconnectInitial(Duration.ofMillis(10));
        properties.setReconnectMax(Duration.ofMillis(20));
        AtomicInteger attempts = new AtomicInteger();
        AtomicInteger successes = new AtomicInteger();
        WebSocketConnector connector = (uri, listener, timeout) -> {
            int attempt = attempts.incrementAndGet();
            FakeWebSocket socket = new FakeWebSocket(listener);
            socket.setOnSend(message -> {
                JsonNode frame = read(message);
                if (!"bot.connect".equals(frame.path("method").asText())) {
                    return;
                }
                if (attempt == 1) {
                    socket.emit(errorResponse(frame.path("id").asText(), "already_connected").toString());
                } else {
                    successes.incrementAndGet();
                    socket.emit(connectResponse(frame.path("id").asText(), "tok-2", "bot-2").toString());
                }
            });
            return CompletableFuture.completedFuture(socket);
        };
        BcnWebSocketClient client = client(properties, connector);

        try {
            client.start();
            await(() -> attempts.get() >= 2 && successes.get() >= 1);
            await(() -> Files.exists(properties.getSessionFile()));
        } finally {
            client.stop();
        }
    }

    @Test
    void handshakeRequiresV3AndBothCapabilities() {
        JsonNode valid = connectResponse("connect-1", "tok", "bot");
        assertThat(BcnWebSocketClient.parseConnectResponse(valid, "connect-1").token()).isEqualTo("tok");

        ObjectNode wrongVersion = valid.deepCopy();
        ((ObjectNode) wrongVersion.path("payload")).put("protocol_version", 2);
        assertThatThrownBy(() -> BcnWebSocketClient.parseConnectResponse(wrongVersion, "connect-1"))
                .isInstanceOf(BcnWebSocketClient.HandshakeException.class);

        ObjectNode noUnifiedEvents = valid.deepCopy();
        ((ObjectNode) noUnifiedEvents.path("payload").path("capabilities")).put("unified_run_events", false);
        assertThatThrownBy(() -> BcnWebSocketClient.parseConnectResponse(noUnifiedEvents, "connect-1"))
                .isInstanceOf(BcnWebSocketClient.HandshakeException.class);

        ObjectNode noCanonicalSession = valid.deepCopy();
        ((ObjectNode) noCanonicalSession.path("payload").path("capabilities"))
                .put("canonical_session_id", false);
        assertThatThrownBy(() -> BcnWebSocketClient.parseConnectResponse(noCanonicalSession, "connect-1"))
                .isInstanceOf(BcnWebSocketClient.HandshakeException.class);
    }

    private BcnWebSocketClient client(BcnProperties properties, WebSocketConnector connector) {
        return client(properties, connector, new SessionStore(mapper, properties));
    }

    private BcnWebSocketClient client(BcnProperties properties, WebSocketConnector connector,
            SessionStore sessions) {
        RecordingFrameSender dispatcherSender = new RecordingFrameSender();
        RunRegistry runs = new RunRegistry();
        ChatSendHandler handler = task -> { };
        BcnDispatcher dispatcher = new BcnDispatcher(mapper, dispatcherSender, runs, handler,
                new ChatAbortHandler(mapper, dispatcherSender, runs));
        return new BcnWebSocketClient(mapper, properties, sessions, connector, dispatcher);
    }

    private BcnProperties properties(Path tempDir) {
        BcnProperties properties = new BcnProperties();
        properties.setUrl(java.net.URI.create("ws://127.0.0.1:18080/ws/bot"));
        properties.setSessionFile(tempDir.resolve(".bcs/session.json"));
        properties.setHeartbeatInterval(Duration.ofMillis(50));
        properties.setConnectTimeout(Duration.ofSeconds(1));
        properties.setReconnectInitial(Duration.ofMillis(10));
        properties.setReconnectMax(Duration.ofMillis(20));
        return properties;
    }

    private ObjectNode connectResponse(String requestId, String token, String botUuid) {
        ObjectNode response = mapper.createObjectNode();
        response.put("type", "res");
        response.put("id", requestId);
        response.put("ok", true);
        ObjectNode payload = response.putObject("payload");
        payload.put("is_new", false);
        payload.put("token", token);
        payload.put("bot_uuid", botUuid);
        payload.put("protocol_version", 3);
        ObjectNode capabilities = payload.putObject("capabilities");
        capabilities.put("unified_run_events", true);
        capabilities.put("canonical_session_id", true);
        return response;
    }

    private ObjectNode errorResponse(String requestId, String code) {
        ObjectNode response = mapper.createObjectNode();
        response.put("type", "res");
        response.put("id", requestId);
        response.put("ok", false);
        response.putObject("error").put("code", code).put("message", code).put("retryable", true);
        return response;
    }

    private JsonNode read(String json) {
        try {
            return mapper.readTree(json);
        } catch (Exception e) {
            throw new IllegalArgumentException(e);
        }
    }

    private static void await(BooleanSupplier condition) throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3);
        while (!condition.getAsBoolean() && System.nanoTime() < deadline) {
            Thread.sleep(10);
        }
        assertThat(condition.getAsBoolean()).isTrue();
    }
}
