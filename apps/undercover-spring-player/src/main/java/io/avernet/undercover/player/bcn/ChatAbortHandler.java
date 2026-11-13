package io.avernet.undercover.player.bcn;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.avernet.undercover.player.protocol.BcnFrames;
import io.avernet.undercover.player.run.PlayerRun;
import io.avernet.undercover.player.run.RunRegistry;
import io.avernet.undercover.player.run.RunRegistry.AbortOutcome;
import io.avernet.undercover.player.run.RunRegistry.AbortStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

@Component
public class ChatAbortHandler {

    private static final Logger log = LoggerFactory.getLogger(ChatAbortHandler.class);

    private final ObjectMapper mapper;
    private final FrameSender sender;
    private final RunRegistry runs;

    public ChatAbortHandler(ObjectMapper mapper, FrameSender sender, RunRegistry runs) {
        this.mapper = mapper;
        this.sender = sender;
        this.runs = runs;
    }

    public void handle(JsonNode request) {
        String requestId = BcnFrames.text(request, "id");
        JsonNode params = request.get("params");
        if (params == null || !params.isObject()) {
            sendInvalid(requestId, "chat.abort params must be an object");
            return;
        }
        String runId = BcnFrames.text(params, "run_id");
        String sessionKey = BcnFrames.text(params, "session_key");
        if (runId.isEmpty() || sessionKey.isEmpty()) {
            sendInvalid(requestId, "chat.abort requires non-empty session_key and run_id");
            return;
        }

        AbortOutcome outcome = runs.abort(runId, sessionKey);
        switch (outcome.status()) {
            case ABORTED -> sendAborted(requestId, outcome.run());
            case ALREADY_TERMINAL -> sendAlreadyTerminal(requestId);
            case SCOPE_MISMATCH -> sender.send(BcnFrames.error(mapper, requestId, "SCOPE_MISMATCH",
                    "run_id does not belong to session_key"));
            case NOT_FOUND -> sender.send(BcnFrames.error(mapper, requestId, "RUN_NOT_FOUND",
                    "Unknown run_id"));
        }
    }

    private void sendAborted(String requestId, PlayerRun run) {
        try {
            run.cancel();
        } catch (RuntimeException e) {
            log.warn("Cancellation callback failed for run_id={}", run.runId(), e);
        }
        int seq = run.nextTerminalSeq();
        try {
            sender.send(BcnFrames.abortedEvent(mapper, run, seq, System.currentTimeMillis()));
        } catch (RuntimeException e) {
            log.warn("Cannot send aborted chat event for run_id={}", run.runId(), e);
        }

        ObjectNode payload = mapper.createObjectNode();
        payload.put("aborted", true);
        payload.putArray("aborted_run_ids").add(run.runId());
        sender.send(BcnFrames.success(mapper, requestId, payload));
    }

    private void sendAlreadyTerminal(String requestId) {
        ObjectNode payload = mapper.createObjectNode();
        payload.put("aborted", false);
        payload.putArray("aborted_run_ids");
        sender.send(BcnFrames.success(mapper, requestId, payload));
    }

    private void sendInvalid(String requestId, String message) {
        sender.send(BcnFrames.error(mapper, requestId, "INVALID_REQUEST", message));
    }
}
