package bcn.a2a.server;

import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import bcn.a2a.spi.BcnCall;
import bcn.a2a.spi.Messages;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.MessageSendParams;

/** {@link BcnCall} 的实现。所有字段在 RequestPipeline.prepare 中一次算好，之后只读。 */
record DefaultBcnCall(String agentId, MessageSendParams request, String contextId, String taskIdOrNull,
                      Map<String, Object> metadata, Set<String> activatedExtensions,
                      CancelSignal cancel) implements BcnCall {

    @Override
    public Message message() {
        return request.message();
    }

    @Override
    public String text() {
        return Messages.textOf(request.message());
    }

    @Override
    public Optional<String> taskId() {
        return Optional.ofNullable(taskIdOrNull);
    }

    @Override
    public boolean isCancelled() {
        return cancel.isCancelled();
    }

    @Override
    public Instant deadline() {
        return cancel.deadline();
    }
}
