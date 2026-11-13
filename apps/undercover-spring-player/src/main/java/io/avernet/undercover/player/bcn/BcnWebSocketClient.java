package io.avernet.undercover.player.bcn;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.avernet.undercover.player.config.BcnProperties;
import io.avernet.undercover.player.protocol.BcnFrames;
import jakarta.annotation.PreDestroy;
import java.net.http.WebSocket;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Component;

@Component
public class BcnWebSocketClient implements FrameSender {

    private static final Logger log = LoggerFactory.getLogger(BcnWebSocketClient.class);

    private final ObjectMapper mapper;
    private final BcnProperties properties;
    private final SessionStore sessions;
    private final WebSocketConnector connector;
    private final BcnDispatcher dispatcher;
    private final ScheduledExecutorService scheduler;
    private final AtomicReference<WebSocket> socket = new AtomicReference<>();
    private final AtomicReference<ScheduledFuture<?>> heartbeat = new AtomicReference<>();
    private final AtomicBoolean running = new AtomicBoolean();
    private final AtomicBoolean connected = new AtomicBoolean();
    private final AtomicBoolean reconnectScheduled = new AtomicBoolean();
    private final AtomicInteger reconnectAttempt = new AtomicInteger();
    private final AtomicLong requestCounter = new AtomicLong();
    private final Object sendLock = new Object();

    public BcnWebSocketClient(ObjectMapper mapper, BcnProperties properties, SessionStore sessions,
            WebSocketConnector connector, @Lazy BcnDispatcher dispatcher) {
        this.mapper = mapper;
        this.properties = properties;
        this.sessions = sessions;
        this.connector = connector;
        this.dispatcher = dispatcher;
        this.scheduler = Executors.newSingleThreadScheduledExecutor(task -> {
            Thread thread = new Thread(task, "bcn-websocket");
            thread.setDaemon(false);
            return thread;
        });
    }

    @EventListener(ApplicationReadyEvent.class)
    public void start() {
        if (running.compareAndSet(false, true)) {
            scheduler.execute(this::connect);
        }
    }

    @PreDestroy
    public void stop() {
        running.set(false);
        connected.set(false);
        cancelHeartbeat();
        WebSocket current = socket.getAndSet(null);
        if (current != null) {
            try {
                current.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown");
            } catch (RuntimeException e) {
                log.debug("Error closing BCN WebSocket during shutdown", e);
            }
        }
        scheduler.shutdownNow();
    }

    @Override
    public void send(JsonNode frame) {
        WebSocket current = socket.get();
        if (current == null) {
            throw new IllegalStateException("BCN WebSocket is not connected");
        }
        sendTo(current, frame);
    }

    static Handshake parseConnectResponse(JsonNode response, String requestId) {
        if (response == null || !"res".equals(BcnFrames.text(response, "type"))) {
            throw new HandshakeException("INVALID_RESPONSE", "bot.connect response must be a res frame");
        }
        if (!requestId.equals(BcnFrames.text(response, "id"))) {
            throw new HandshakeException("INVALID_RESPONSE", "bot.connect response id mismatch");
        }
        if (!response.path("ok").asBoolean(false)) {
            String code = BcnFrames.text(response.path("error"), "code");
            String message = BcnFrames.text(response.path("error"), "message");
            throw new HandshakeException(code.isEmpty() ? "CONNECT_FAILED" : code, message);
        }

        JsonNode payload = response.get("payload");
        if (payload == null || !payload.isObject()) {
            throw new HandshakeException("INVALID_RESPONSE", "bot.connect payload must be an object");
        }
        if (payload.path("protocol_version").asInt(-1) != 3) {
            throw new HandshakeException("UNSUPPORTED_PROTOCOL_VERSION",
                    "BCN did not negotiate protocol version 3");
        }
        JsonNode capabilities = payload.get("capabilities");
        if (capabilities == null || !capabilities.isObject()
                || !capabilities.path("unified_run_events").asBoolean(false)) {
            throw new HandshakeException("UNSUPPORTED_CAPABILITY",
                    "BCN did not enable unified_run_events");
        }
        if (!capabilities.path("canonical_session_id").asBoolean(false)) {
            throw new HandshakeException("UNSUPPORTED_CAPABILITY",
                    "BCN did not enable canonical_session_id");
        }
        String token = BcnFrames.text(payload, "token");
        String botUuid = BcnFrames.text(payload, "bot_uuid");
        if (token.isEmpty() || botUuid.isEmpty()) {
            throw new HandshakeException("INVALID_RESPONSE", "bot.connect payload requires token and bot_uuid");
        }
        return new Handshake(token, botUuid);
    }

    private void connect() {
        if (!running.get()) {
            return;
        }
        String requestId = "connect-" + requestCounter.incrementAndGet();
        CompletableFuture<JsonNode> handshake = new CompletableFuture<>();
        WebSocket current = null;
        try {
            WebSocket.Listener listener = listener(requestId, handshake);
            current = connector.connect(properties.getUrl(), listener, properties.getConnectTimeout())
                    .get(properties.getConnectTimeout().toMillis() + 1_000, TimeUnit.MILLISECONDS);
            socket.set(current);
            sendConnectFrame(current, requestId);

            JsonNode response = handshake.get(properties.getConnectTimeout().toMillis(), TimeUnit.MILLISECONDS);
            Handshake result = parseConnectResponse(response, requestId);
            sessions.save(result.token(), result.botUuid());

            connected.set(true);
            reconnectAttempt.set(0);
            scheduleHeartbeat();
            log.info("Connected to BCN at {} as bot {}", properties.getUrl(), result.botUuid());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (ExecutionException e) {
            fail(current, handshake, e.getCause() == null ? e : e.getCause());
        } catch (TimeoutException e) {
            fail(current, handshake, new IllegalStateException("BCN handshake timed out", e));
        } catch (RuntimeException e) {
            fail(current, handshake, e);
        }
    }

    private void sendConnectFrame(WebSocket current, String requestId) {
        Optional<SessionStore.Session> saved = sessions.load();
        String configuredToken = properties.getToken() == null ? "" : properties.getToken().trim();
        String token = configuredToken.isEmpty()
                ? saved.map(SessionStore.Session::token).orElse("")
                : configuredToken;
        String configuredBotId = properties.getBotId() == null ? "" : properties.getBotId().trim();
        String botId = configuredBotId.isEmpty()
                ? saved.map(SessionStore.Session::botUuid).orElse("")
                : configuredBotId;

        ObjectNode frame = mapper.createObjectNode();
        frame.put("type", "req");
        frame.put("id", requestId);
        frame.put("method", "bot.connect");
        ObjectNode params = frame.putObject("params");
        if (!botId.isEmpty()) {
            params.put("bot_id", botId);
        }
        if (!token.isEmpty()) {
            params.put("token", token);
        }
        params.put("protocol_version", 3);
        params.put("client_kind", properties.getClientKind());
        sendTo(current, frame);
    }

    private WebSocket.Listener listener(String requestId, CompletableFuture<JsonNode> handshake) {
        return new WebSocket.Listener() {
            private final StringBuilder partial = new StringBuilder();

            @Override
            public void onOpen(WebSocket webSocket) {
                webSocket.request(1);
            }

            @Override
            public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
                partial.append(data);
                if (last) {
                    String text = partial.toString();
                    partial.setLength(0);
                    handleText(webSocket, text, requestId, handshake);
                }
                webSocket.request(1);
                return null;
            }

            @Override
            public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
                handshake.completeExceptionally(new IllegalStateException(
                        "BCN socket closed during handshake: " + statusCode + " " + reason));
                handleDisconnect(webSocket, null);
                return null;
            }

            @Override
            public void onError(WebSocket webSocket, Throwable error) {
                handshake.completeExceptionally(error);
                webSocket.abort();
                handleDisconnect(webSocket, error);
            }
        };
    }

    private void handleText(WebSocket webSocket, String text, String requestId,
            CompletableFuture<JsonNode> handshake) {
        final JsonNode frame;
        try {
            frame = mapper.readTree(text);
        } catch (Exception e) {
            log.warn("Ignoring malformed BCN WebSocket frame", e);
            return;
        }
        String type = BcnFrames.text(frame, "type");
        if ("res".equals(type)) {
            if (requestId.equals(BcnFrames.text(frame, "id")) && !handshake.isDone()) {
                handshake.complete(frame);
            }
            return;
        }
        if ("req".equals(type) && socket.get() == webSocket) {
            try {
                dispatcher.dispatch(frame);
            } catch (RuntimeException e) {
                log.error("Failed to dispatch BCN request", e);
            }
        }
    }

    private void scheduleHeartbeat() {
        long interval = Math.max(1, properties.getHeartbeatInterval().toMillis());
        ScheduledFuture<?> next = scheduler.scheduleAtFixedRate(this::sendHeartbeat, interval, interval,
                TimeUnit.MILLISECONDS);
        ScheduledFuture<?> previous = heartbeat.getAndSet(next);
        if (previous != null) {
            previous.cancel(false);
        }
    }

    private void sendHeartbeat() {
        if (!connected.get()) {
            return;
        }
        ObjectNode frame = mapper.createObjectNode();
        frame.put("type", "req");
        frame.put("id", "status-" + requestCounter.incrementAndGet());
        frame.put("method", "bot.status");
        frame.putObject("params");
        try {
            send(frame);
        } catch (RuntimeException e) {
            handleDisconnect(socket.get(), e);
        }
    }

    private void fail(WebSocket failedSocket, CompletableFuture<JsonNode> handshake, Throwable error) {
        if (!handshake.isDone()) {
            handshake.completeExceptionally(error);
        }
        handleDisconnect(failedSocket, error);
        if (failedSocket != null) {
            try {
                failedSocket.abort();
            } catch (RuntimeException ignored) {
                // The socket is already unusable.
            }
        }
    }

    private void handleDisconnect(WebSocket failedSocket, Throwable cause) {
        if (failedSocket != null && !socket.compareAndSet(failedSocket, null)) {
            return;
        }
        connected.set(false);
        cancelHeartbeat();
        if (cause != null) {
            String detail = cause instanceof HandshakeException handshakeError
                    ? handshakeDetail(handshakeError)
                    : cause.toString();
            log.warn("BCN connection lost: {}", detail);
        }
        scheduleReconnect();
    }

    private void scheduleReconnect() {
        if (!running.get() || !reconnectScheduled.compareAndSet(false, true)) {
            return;
        }
        long delay = reconnectDelayMillis(reconnectAttempt.getAndIncrement());
        scheduler.schedule(() -> {
            reconnectScheduled.set(false);
            connect();
        }, delay, TimeUnit.MILLISECONDS);
    }

    private static String handshakeDetail(HandshakeException error) {
        String code = error.code();
        String message = error.getMessage();
        return message == null || message.equals(code) ? code : code + ": " + message;
    }

    private long reconnectDelayMillis(int attempt) {
        long initial = Math.max(1, properties.getReconnectInitial().toMillis());
        long maximum = Math.max(initial, properties.getReconnectMax().toMillis());
        long delay = initial;
        for (int i = 0; i < attempt && delay < maximum; i++) {
            delay = Math.min(maximum, delay * 2);
        }
        return delay;
    }

    private void cancelHeartbeat() {
        ScheduledFuture<?> current = heartbeat.getAndSet(null);
        if (current != null) {
            current.cancel(false);
        }
    }

    private void sendTo(WebSocket target, JsonNode frame) {
        final String json;
        try {
            json = mapper.writeValueAsString(frame);
        } catch (Exception e) {
            throw new IllegalArgumentException("Cannot serialize BCN frame", e);
        }
        synchronized (sendLock) {
            target.sendText(json, true).whenComplete((ignored, error) -> {
                if (error != null) {
                    handleDisconnect(target, error);
                }
            });
        }
    }

    record Handshake(String token, String botUuid) {
    }

    static final class HandshakeException extends RuntimeException {

        private final String code;

        HandshakeException(String code, String message) {
            super(message);
            this.code = code;
        }

        String code() {
            return code;
        }
    }
}
