package a2a.agent.client;

/**
 * 多轮对话：每次调用自动带上上一次回复的 contextId。
 *
 * <p>一个会话对象代表一段对话，<b>不要在多个线程上并发调用同一个会话</b>（并发时 contextId 的先后无法保证）；
 * 不同对话各用一个会话对象即可，它们共享同一个 {@link RemoteAgent}。
 */
public final class Conversation {

    private final RemoteAgent agent;
    private volatile String contextId;

    Conversation(RemoteAgent agent) {
        this.agent = agent;
    }

    public Reply send(String text) {
        Reply reply = agent.send(AgentRequest.text(text).contextId(contextId).build());
        remember(reply);
        return reply;
    }

    public StreamHandle stream(String text, StreamListener listener) {
        StreamHandle handle = agent.stream(AgentRequest.text(text).contextId(contextId).build(), listener);
        handle.completion().thenAccept(this::remember);
        return handle;
    }

    /** 当前的 contextId；第一轮之前为 null。 */
    public String contextId() {
        return contextId;
    }

    private void remember(Reply reply) {
        if (reply.contextId() != null) {
            contextId = reply.contextId();
        }
    }
}
