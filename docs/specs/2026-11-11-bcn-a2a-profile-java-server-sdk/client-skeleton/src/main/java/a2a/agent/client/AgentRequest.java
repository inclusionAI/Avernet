package a2a.agent.client;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import a2a.agent.client.internal.PayloadKeys;

/**
 * 一次调用的内容。最常用的写法是 {@code AgentRequest.text("…").build()}。
 *
 * <p>本期只发送文本（Payload 规范第 5 节）。业务字段放进请求级 metadata，键名不能使用平台保留的前缀。
 */
public final class AgentRequest {

    private final String text;
    private final String contextId;
    private final String taskId;
    private final List<String> referenceTaskIds;
    private final Map<String, Object> metadata;
    private final Duration timeout;
    private final Boolean toolCalls;

    private AgentRequest(Builder b) {
        this.text = b.text;
        this.contextId = b.contextId;
        this.taskId = b.taskId;
        this.referenceTaskIds = List.copyOf(b.referenceTaskIds);
        this.metadata = Collections.unmodifiableMap(new LinkedHashMap<>(b.metadata));
        this.timeout = b.timeout;
        this.toolCalls = b.toolCalls;
    }

    public static Builder text(String text) {
        return new Builder(text);
    }

    public String text() {
        return text;
    }

    public String contextId() {
        return contextId;
    }

    public String taskId() {
        return taskId;
    }

    public List<String> referenceTaskIds() {
        return referenceTaskIds;
    }

    public Map<String, Object> metadata() {
        return metadata;
    }

    /** null 表示使用 {@link ClientOptions} 中的默认值。 */
    public Duration timeout() {
        return timeout;
    }

    /** null 表示使用 {@link ClientOptions#requestToolCalls()}。 */
    public Boolean toolCalls() {
        return toolCalls;
    }

    public static final class Builder {
        private final String text;
        private String contextId;
        private String taskId;
        private final List<String> referenceTaskIds = new ArrayList<>();
        private final Map<String, Object> metadata = new LinkedHashMap<>();
        private Duration timeout;
        private Boolean toolCalls;

        private Builder(String text) {
            this.text = text;
        }

        /** 多轮对话：沿用上一次回复的 contextId。也可以直接用 {@link RemoteAgent#conversation()}。 */
        public Builder contextId(String contextId) {
            this.contextId = contextId;
            return this;
        }

        /** 续交已有的 Task。只有引擎模式（支持查询）的 Agent 能识别，其他 Agent 会返回 TASK_NOT_FOUND。 */
        public Builder taskId(String taskId) {
            this.taskId = taskId;
            return this;
        }

        public Builder referenceTaskId(String taskId) {
            this.referenceTaskIds.add(taskId);
            return this;
        }

        /**
         * 请求级业务字段。值可以是 String、Boolean、Number、Map、List；
         * 超过 2^53 的整数和金额请用字符串（Payload 规范 PL-35，线上是 protobuf Struct 中的 double）。
         */
        public Builder metadata(String key, Object value) {
            this.metadata.put(key, value);
            return this;
        }

        public Builder timeout(Duration timeout) {
            this.timeout = timeout;
            return this;
        }

        /** 是否请求工具调用记录（toolcall 扩展）。不设置时使用客户端默认值。 */
        public Builder toolCalls(boolean enabled) {
            this.toolCalls = enabled;
            return this;
        }

        public AgentRequest build() {
            if (text == null || text.isBlank()) {
                throw new IllegalArgumentException("text is required");
            }
            for (String key : metadata.keySet()) {
                if (PayloadKeys.isReserved(key)) {
                    throw new IllegalArgumentException("metadata key is reserved for the platform or extensions: " + key);
                }
            }
            return new AgentRequest(this);
        }
    }
}
