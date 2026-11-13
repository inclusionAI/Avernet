package io.avernet.undercover.player.bcn;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.avernet.undercover.player.protocol.BcnFrames;
import io.avernet.undercover.player.run.PlayerRun;
import io.avernet.undercover.player.run.RunRegistry;
import io.avernet.undercover.player.run.RunRegistry.RunRegistration;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

@Component
public class BcnDispatcher {

    private static final Logger log = LoggerFactory.getLogger(BcnDispatcher.class);

    private final ObjectMapper mapper;
    private final FrameSender sender;
    private final RunRegistry runs;
    private final ChatSendHandler chatSendHandler;
    private final ChatAbortHandler abortHandler;

    public BcnDispatcher(ObjectMapper mapper, FrameSender sender, RunRegistry runs,
            ChatSendHandler chatSendHandler, ChatAbortHandler abortHandler) {
        this.mapper = mapper;
        this.sender = sender;
        this.runs = runs;
        this.chatSendHandler = chatSendHandler;
        this.abortHandler = abortHandler;
    }

    public void dispatch(JsonNode request) {
        String requestId = BcnFrames.text(request, "id");
        String method = BcnFrames.text(request, "method");
        switch (method) {
            case "chat.send" -> handleChatSend(requestId, request.get("params"));
            case "chat.inject" -> handleChatInject(requestId, request.get("params"));
            case "chat.abort" -> abortHandler.handle(request);
            case "chat.history" -> handleChatHistory(requestId, request.get("params"));
            default -> sender.send(BcnFrames.error(mapper, requestId, "unknown_method",
                    "Unsupported method: " + method));
        }
    }

    private void handleChatSend(String requestId, JsonNode params) {
        String sessionKey = validateCanonicalSession(requestId, params);
        if (sessionKey == null) {
            return;
        }
        String text = extractText(params.path("message").get("content"));
        if (text.isBlank()) {
            sendInvalid(requestId, "Empty message");
            return;
        }

        String runId = BcnFrames.text(params, "idempotency_key");
        if (runId.isEmpty()) {
            runId = requestId.isEmpty() ? UUID.randomUUID().toString() : requestId;
        }
        RunRegistration registration = runs.register(runId, sessionKey, BcnFrames.text(params, "bcs_session_id"));

        ObjectNode payload = mapper.createObjectNode();
        payload.put("run_id", runId);
        try {
            sender.send(BcnFrames.success(mapper, requestId, payload));
        } catch (RuntimeException e) {
            if (registration.created()) {
                runs.claimTerminal(registration.run());
                registration.run().cancel();
            }
            throw e;
        }

        if (registration.created()) {
            try {
                chatSendHandler.handle(new ChatSendHandler.ChatSendTask(registration.run(), text, params));
            } catch (RuntimeException e) {
                log.error("chat.send handler failed for run_id={}", runId, e);
                terminateWithError(registration.run(), e);
            }
        }
    }

    private void terminateWithError(PlayerRun run, RuntimeException error) {
        if (!runs.claimTerminal(run)) {
            return;
        }
        run.cancel();
        int seq = run.nextTerminalSeq();
        String message = error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
        try {
            sender.send(BcnFrames.errorEvent(mapper, run, seq, System.currentTimeMillis(),
                    "HANDLER_ERROR", message));
        } catch (RuntimeException e) {
            log.warn("Cannot send error chat event for run_id={}", run.runId(), e);
        }
    }

    private void handleChatInject(String requestId, JsonNode params) {
        if (validateCanonicalSession(requestId, params) == null) {
            return;
        }
        sender.send(BcnFrames.success(mapper, requestId, mapper.createObjectNode()));
    }

    private void handleChatHistory(String requestId, JsonNode params) {
        JsonNode safeParams = params != null && params.isObject() ? params : mapper.createObjectNode();
        String sessionKey = BcnFrames.text(safeParams, "session_key");
        String sessionId = BcnFrames.text(safeParams, "bcs_session_id");
        ObjectNode payload = mapper.createObjectNode();
        payload.put("session_key", sessionKey);
        payload.put("session_id", sessionId.isEmpty() ? sessionKey : sessionId);
        payload.putArray("messages");
        sender.send(BcnFrames.success(mapper, requestId, payload));
    }

    private String validateCanonicalSession(String requestId, JsonNode params) {
        if (params == null || !params.isObject()) {
            sendInvalid(requestId, "params must be an object");
            return null;
        }
        String sessionId = BcnFrames.text(params, "bcs_session_id");
        if (sessionId.isEmpty()) {
            sendInvalid(requestId, "BCN V3 requires non-empty bcs_session_id");
            return null;
        }
        String sessionKey = BcnFrames.text(params, "session_key");
        if (!sessionId.equals(sessionKey)) {
            sendInvalid(requestId, "session_key must equal bcs_session_id");
            return null;
        }
        return sessionKey;
    }

    private String extractText(JsonNode content) {
        if (content == null || !content.isArray()) {
            return "";
        }
        StringBuilder text = new StringBuilder();
        for (JsonNode block : content) {
            if ("text".equals(BcnFrames.text(block, "type"))) {
                String value = block.path("text").asText("");
                if (!value.isEmpty()) {
                    if (!text.isEmpty()) {
                        text.append('\n');
                    }
                    text.append(value);
                }
            }
        }
        return text.toString();
    }

    private void sendInvalid(String requestId, String message) {
        sender.send(BcnFrames.error(mapper, requestId, "INVALID_REQUEST", message));
    }
}
