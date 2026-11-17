package bcn.a2a.server;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.a2aproject.sdk.spec.Artifact;
import org.a2aproject.sdk.spec.Part;
import org.a2aproject.sdk.spec.StreamingEventKind;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskArtifactUpdateEvent;
import org.a2aproject.sdk.spec.TaskStatusUpdateEvent;

/**
 * 非流式 SendMessage 调用流式档位的 Agent 时使用：运行 onStream，把所有事件折叠成最终的 Task 快照返回。
 * 这样一个 onStream 实现同时服务流式和非流式两种调用。
 *
 * <p>工具观察、过程说明属于过程信息，只体现在中间状态里，最终快照只保留最后的状态和全部产出物。
 *
 * <p>TODO(待定)：请求 {@code configuration.returnImmediately=true} 时，第二档的 Task 无法事后查询，
 * 立即返回没有意义。按 Profile 决定是拒绝还是忽略该参数。
 */
final class CollectingStream extends TaskStreamWriter {

    private Task task;
    private final Map<String, Artifact> artifacts = new LinkedHashMap<>();

    CollectingStream(String contextId, Set<String> activated, CancelSignal cancel, boolean engineMode) {
        super(contextId, activated, cancel, engineMode);
    }

    @Override
    protected void emit(StreamingEventKind event) {
        if (event instanceof Task snapshot) {
            task = snapshot;
            if (snapshot.artifacts() != null) {
                snapshot.artifacts().forEach(a -> artifacts.put(a.artifactId(), a));
            }
        } else if (event instanceof TaskArtifactUpdateEvent update) {
            Artifact incoming = update.artifact();
            Artifact existing = artifacts.get(incoming.artifactId());
            if (Boolean.TRUE.equals(update.append()) && existing != null) {
                List<Part<?>> parts = new ArrayList<>(existing.parts());
                parts.addAll(incoming.parts());
                // TODO(实现)：相邻的 TextPart 合并成一段，避免最终快照里出现大量碎片。
                artifacts.put(incoming.artifactId(), Artifact.builder(existing).parts(parts).build());
            } else {
                artifacts.put(incoming.artifactId(), incoming);
            }
        } else if (event instanceof TaskStatusUpdateEvent update) {
            task = Task.builder(task).status(update.status()).build();
        }
    }

    @Override
    protected void onClosed() {
        // 结果由 result() 取走，这里不需要做任何事
    }

    /** 最终快照。只能在流结束后调用。 */
    synchronized Task result() {
        if (task == null) {
            throw new IllegalStateException("no task was produced");
        }
        return Task.builder(task).artifacts(List.copyOf(artifacts.values())).build();
    }
}
