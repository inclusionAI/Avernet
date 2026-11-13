package io.avernet.undercover.player.bcn;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.avernet.undercover.player.run.RunRegistry;
import io.avernet.undercover.player.run.RunRegistry.AbortStatus;
import io.avernet.undercover.player.support.RecordingFrameSender;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class BcnDispatcherTest {

    private final ObjectMapper mapper = new ObjectMapper();
    private final RunRegistry runs = new RunRegistry();
    private final RecordingFrameSender sender = new RecordingFrameSender();
    private final AtomicReference<ChatSendHandler.ChatSendTask> handled = new AtomicReference<>();
    private BcnDispatcher dispatcher;

    @BeforeEach
    void setUp() {
        ChatSendHandler sendHandler = handled::set;
        ChatAbortHandler abortHandler = new ChatAbortHandler(mapper, sender, runs);
        dispatcher = new BcnDispatcher(mapper, sender, runs, sendHandler, abortHandler);
    }

    @Test
    void chatSendAcksWithIdempotencyKeyAsRunIdBeforeHandlerRuns() {
        List<String> order = new CopyOnWriteArrayList<>();
        ChatSendHandler sendHandler = task -> {
            handled.set(task);
            order.add("handler");
        };
        FrameSender orderedSender = frame -> {
            sender.send(frame);
            order.add("ack");
        };
        BcnDispatcher ordered = new BcnDispatcher(mapper, orderedSender, runs, sendHandler,
                new ChatAbortHandler(mapper, orderedSender, runs));

        ordered.dispatch(chatSend("chat-1", "idem-1", "session-1", "hello"));

        assertThat(order).containsExactly("ack", "handler");
        JsonNode ack = sender.last();
        assertThat(ack.path("ok").asBoolean()).isTrue();
        assertThat(ack.path("payload").path("run_id").asText()).isEqualTo("idem-1");
        assertThat(handled.get()).isNotNull();
        assertThat(handled.get().run().runId()).isEqualTo("idem-1");
        assertThat(handled.get().run().sessionId()).isEqualTo("session-1");
        assertThat(handled.get().text()).isEqualTo("hello");
    }

    @Test
    void chatSendFallsBackToRequestIdForRunId() {
        dispatcher.dispatch(chatSend("chat-2", "", "session-1", "hello"));

        assertThat(sender.last().path("payload").path("run_id").asText()).isEqualTo("chat-2");
    }

    @Test
    void chatSendRejectsMissingOrMismatchedCanonicalSession() {
        ObjectNode missing = chatSend("chat-1", "run-1", "session-1", "hello");
        ((ObjectNode) missing.get("params")).remove("bcs_session_id");
        dispatcher.dispatch(missing);
        assertThat(sender.last().path("error").path("code").asText()).isEqualTo("INVALID_REQUEST");

        ObjectNode mismatched = chatSend("chat-2", "run-2", "session-1", "hello");
        ((ObjectNode) mismatched.get("params")).put("session_key", "other-session");
        dispatcher.dispatch(mismatched);
        assertThat(sender.last().path("error").path("code").asText()).isEqualTo("INVALID_REQUEST");
    }

    @Test
    void chatSendRejectsEmptyText() {
        dispatcher.dispatch(chatSend("chat-1", "run-1", "session-1", "  "));

        assertThat(sender.last().path("error").path("code").asText()).isEqualTo("INVALID_REQUEST");
        assertThat(handled.get()).isNull();
    }

    @Test
    void handlerFailureTerminatesRunAndEmitsErrorEvent() {
        BcnDispatcher failing = new BcnDispatcher(mapper, sender, runs, task -> {
            throw new IllegalStateException("boom");
        }, new ChatAbortHandler(mapper, sender, runs));

        failing.dispatch(chatSend("chat-1", "run-1", "session-1", "hello"));

        assertThat(sender.frames()).hasSize(2);
        JsonNode error = sender.last();
        assertThat(error.path("event").asText()).isEqualTo("chat");
        assertThat(error.path("payload").path("state").asText()).isEqualTo("error");
        assertThat(error.path("payload").path("errorCode").asText()).isEqualTo("HANDLER_ERROR");
        assertThat(runs.abort("run-1", "session-1").status()).isEqualTo(AbortStatus.ALREADY_TERMINAL);
    }

    @Test
    void ackFailureDoesNotLeaveAnActiveRun() {
        RunRegistry localRuns = new RunRegistry();
        FrameSender offline = frame -> {
            throw new IllegalStateException("offline");
        };
        BcnDispatcher failing = new BcnDispatcher(mapper, offline, localRuns, task -> { },
                new ChatAbortHandler(mapper, offline, localRuns));

        assertThatThrownBy(() -> failing.dispatch(chatSend("chat-1", "run-1", "session-1", "hello")))
                .isInstanceOf(IllegalStateException.class);
        assertThat(localRuns.abort("run-1", "session-1").status())
                .isEqualTo(AbortStatus.ALREADY_TERMINAL);
    }

    @Test
    void duplicateAckFailureDoesNotCancelTheOriginalRun() {
        RunRegistry localRuns = new RunRegistry();
        localRuns.register("run-1", "session-1", "session-1");
        FrameSender offline = frame -> {
            throw new IllegalStateException("offline");
        };
        BcnDispatcher failing = new BcnDispatcher(mapper, offline, localRuns, task -> {
            throw new AssertionError("duplicate chat.send must not be handled");
        }, new ChatAbortHandler(mapper, offline, localRuns));

        assertThatThrownBy(() -> failing.dispatch(chatSend("chat-1", "run-1", "session-1", "hello")))
                .isInstanceOf(IllegalStateException.class);
        assertThat(localRuns.abort("run-1", "session-1").status()).isEqualTo(AbortStatus.ABORTED);
    }

    @Test
    void chatInjectAcksWithoutRegisteringARun() {
        dispatcher.dispatch(chatRequest("inject-1", "chat.inject", "session-1"));

        JsonNode ack = sender.last();
        assertThat(ack.path("ok").asBoolean()).isTrue();
        assertThat(ack.path("payload").isObject()).isTrue();
        assertThat(ack.path("payload").size()).isZero();
        assertThat(runs.abort("inject-1", "session-1").status()).isEqualTo(AbortStatus.NOT_FOUND);
        assertThat(handled.get()).isNull();
    }

    @Test
    void chatInjectRejectsMissingCanonicalSession() {
        ObjectNode request = chatRequest("inject-1", "chat.inject", "session-1");
        ((ObjectNode) request.get("params")).remove("bcs_session_id");

        dispatcher.dispatch(request);

        assertThat(sender.last().path("error").path("code").asText()).isEqualTo("INVALID_REQUEST");
    }

    @Test
    void chatHistoryReturnsAnEmptyMessageList() {
        dispatcher.dispatch(chatRequest("history-1", "chat.history", "session-1"));

        JsonNode payload = sender.last().path("payload");
        assertThat(payload.path("session_key").asText()).isEqualTo("session-1");
        assertThat(payload.path("messages").isArray()).isTrue();
        assertThat(payload.path("messages")).isEmpty();
    }

    private ObjectNode chatSend(String requestId, String idempotencyKey, String sessionId, String text) {
        ObjectNode request = chatRequest(requestId, "chat.send", sessionId);
        ObjectNode params = (ObjectNode) request.get("params");
        if (!idempotencyKey.isEmpty()) {
            params.put("idempotency_key", idempotencyKey);
        }
        ObjectNode message = params.putObject("message");
        message.put("role", "user");
        message.putArray("content").addObject().put("type", "text").put("text", text);
        return request;
    }

    private ObjectNode chatRequest(String requestId, String method, String sessionId) {
        ObjectNode request = mapper.createObjectNode();
        request.put("type", "req");
        request.put("id", requestId);
        request.put("method", method);
        ObjectNode params = request.putObject("params");
        params.put("session_key", sessionId);
        params.put("bcs_session_id", sessionId);
        params.put("bcs_group_id", "group-1");
        return request;
    }
}
