package io.avernet.undercover.player.protocol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.avernet.undercover.player.run.PlayerRun;

public final class BcnFrames {

    private BcnFrames() {
    }

    public static ObjectNode success(ObjectMapper mapper, String requestId, ObjectNode payload) {
        ObjectNode frame = mapper.createObjectNode();
        frame.put("type", "res");
        frame.put("id", requestId);
        frame.put("ok", true);
        frame.set("payload", payload);
        return frame;
    }

    public static ObjectNode error(ObjectMapper mapper, String requestId, String code, String message) {
        ObjectNode frame = mapper.createObjectNode();
        frame.put("type", "res");
        frame.put("id", requestId);
        frame.put("ok", false);
        ObjectNode error = frame.putObject("error");
        error.put("code", code);
        error.put("message", message);
        error.put("retryable", false);
        return frame;
    }

    public static ObjectNode abortedEvent(ObjectMapper mapper, PlayerRun run, int seq, long timestamp) {
        ObjectNode frame = chatFrame(mapper, run, seq, timestamp, "aborted");
        frame.withObject("/payload").put("stopReason", "aborted");
        return frame;
    }

    public static ObjectNode finalEvent(ObjectMapper mapper, PlayerRun run, int seq, long timestamp,
            String content) {
        ObjectNode frame = chatFrame(mapper, run, seq, timestamp, "final");
        ObjectNode payload = frame.withObject("/payload");
        payload.put("content", content);
        payload.put("stopReason", "complete");
        return frame;
    }

    public static ObjectNode errorEvent(ObjectMapper mapper, PlayerRun run, int seq, long timestamp,
            String code, String message) {
        ObjectNode frame = chatFrame(mapper, run, seq, timestamp, "error");
        ObjectNode payload = frame.withObject("/payload");
        payload.put("errorCode", code);
        payload.put("errorMessage", message);
        return frame;
    }

    private static ObjectNode chatFrame(ObjectMapper mapper, PlayerRun run, int seq, long timestamp,
            String state) {
        ObjectNode frame = mapper.createObjectNode();
        frame.put("type", "event");
        frame.put("event", "chat");
        frame.put("seq", seq);

        ObjectNode payload = frame.putObject("payload");
        payload.put("runId", run.runId());
        payload.put("sessionId", run.sessionId());
        payload.put("seq", seq);
        payload.put("ts", timestamp);
        payload.put("state", state);
        return frame;
    }

    public static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value != null && value.isTextual() ? value.asText().trim() : "";
    }
}
