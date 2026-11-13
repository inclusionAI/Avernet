package io.avernet.undercover.player.bcn;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.avernet.undercover.player.run.PlayerRun;
import io.avernet.undercover.player.run.RunRegistry;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import reactor.core.Disposable;

class ChatAbortHandlerTest {

    private final ObjectMapper mapper = new ObjectMapper();
    private final RunRegistry runs = new RunRegistry();
    private final List<JsonNode> frames = new CopyOnWriteArrayList<>();
    private final List<String> order = new CopyOnWriteArrayList<>();
    private ChatAbortHandler handler;

    @BeforeEach
    void setUp() {
        FrameSender sender = frame -> {
            frames.add(frame);
            order.add("event".equals(frame.path("type").asText()) ? "event" : "response");
        };
        handler = new ChatAbortHandler(mapper, sender, runs);
    }

    @Test
    void disposesStreamAfterClaimingTerminalAndBeforeEmittingEventAndResponse() {
        PlayerRun run = runs.register("run-1", "session-1", "session-1").run();
        FakeDisposable disposable = new FakeDisposable(run, order);
        run.setDisposable(disposable);

        handler.handle(abortRequest("abort-1", "session-1", "run-1"));

        assertThat(disposable.disposeCount()).isEqualTo(1);
        assertThat(order).containsExactly("disposed", "event", "response");
        assertThat(frames).hasSize(2);

        JsonNode event = frames.get(0);
        assertThat(event.path("event").asText()).isEqualTo("chat");
        assertThat(event.path("payload").path("state").asText()).isEqualTo("aborted");
        assertThat(event.path("payload").path("runId").asText()).isEqualTo("run-1");
        assertThat(event.path("payload").path("sessionId").asText()).isEqualTo("session-1");
        assertThat(event.path("payload").path("seq").asInt()).isEqualTo(event.path("seq").asInt());

        JsonNode response = frames.get(1);
        assertThat(response.path("ok").asBoolean()).isTrue();
        assertThat(response.path("payload").path("aborted").asBoolean()).isTrue();
        assertThat(response.path("payload").path("aborted_run_ids").get(0).asText()).isEqualTo("run-1");
    }

    @Test
    void abortIsIdempotentAndNeverDisposesTwice() {
        PlayerRun run = runs.register("run-1", "session-1", "session-1").run();
        FakeDisposable disposable = new FakeDisposable(run, order);
        run.setDisposable(disposable);

        handler.handle(abortRequest("abort-1", "session-1", "run-1"));
        handler.handle(abortRequest("abort-2", "session-1", "run-1"));

        assertThat(disposable.disposeCount()).isEqualTo(1);
        assertThat(frames).hasSize(3);
        assertThat(frames.get(2).path("payload").path("aborted").asBoolean()).isFalse();
    }

    @Test
    void streamRegisteredAfterAbortIsDisposedImmediately() {
        PlayerRun run = runs.register("run-1", "session-1", "session-1").run();
        handler.handle(abortRequest("abort-1", "session-1", "run-1"));

        FakeDisposable disposable = new FakeDisposable(run, order);
        run.setDisposable(disposable);

        assertThat(disposable.disposeCount()).isEqualTo(1);
    }

    @Test
    void alreadyTerminalRunIsIdempotentAndDoesNotEmitAnEvent() {
        PlayerRun run = runs.register("run-1", "session-1", "session-1").run();
        runs.claimTerminal(run);

        handler.handle(abortRequest("abort-1", "session-1", "run-1"));

        assertThat(frames).hasSize(1);
        JsonNode response = frames.get(0);
        assertThat(response.path("ok").asBoolean()).isTrue();
        assertThat(response.path("payload").path("aborted").asBoolean()).isFalse();
        assertThat(response.path("payload").path("aborted_run_ids")).isEmpty();
    }

    @Test
    void unknownAndMismatchedRunsReturnProtocolErrors() {
        runs.register("run-1", "session-1", "session-1");

        handler.handle(abortRequest("abort-1", "session-1", "missing"));
        assertThat(frames.get(frames.size() - 1).path("error").path("code").asText())
                .isEqualTo("RUN_NOT_FOUND");

        handler.handle(abortRequest("abort-2", "other-session", "run-1"));
        assertThat(frames.get(frames.size() - 1).path("error").path("code").asText())
                .isEqualTo("SCOPE_MISMATCH");
    }

    @Test
    void invalidParamsReturnInvalidRequest() {
        ObjectNode request = mapper.createObjectNode();
        request.put("type", "req");
        request.put("id", "abort-1");
        request.put("method", "chat.abort");
        request.putObject("params").put("run_id", "run-1");

        handler.handle(request);

        assertThat(frames.get(0).path("error").path("code").asText()).isEqualTo("INVALID_REQUEST");
    }

    private ObjectNode abortRequest(String id, String sessionKey, String runId) {
        ObjectNode request = mapper.createObjectNode();
        request.put("type", "req");
        request.put("id", id);
        request.put("method", "chat.abort");
        ObjectNode params = request.putObject("params");
        params.put("session_key", sessionKey);
        params.put("run_id", runId);
        return request;
    }

    private record FakeDisposable(PlayerRun run, List<String> order, AtomicInteger count)
            implements Disposable {

        private FakeDisposable(PlayerRun run, List<String> order) {
            this(run, order, new AtomicInteger());
        }

        @Override
        public void dispose() {
            assertThat(run.isTerminal()).isTrue();
            count.incrementAndGet();
            order.add("disposed");
        }

        int disposeCount() {
            return count.get();
        }
    }
}
