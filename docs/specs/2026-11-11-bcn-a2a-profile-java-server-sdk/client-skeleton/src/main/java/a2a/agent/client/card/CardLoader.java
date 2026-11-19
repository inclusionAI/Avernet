package a2a.agent.client.card;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ConcurrentHashMap;

import org.a2aproject.sdk.grpc.AgentCard;

/**
 * 从 URL 拉取 AgentCard，带缓存。本期不支持按 Agent 名从 BCS 查询。
 *
 * <p><b>性能</b>（高并发下 Card 拉取不能成为瓶颈，也不能把对方的 Card 服务打挂）：
 * <ul>
 *   <li>按 URL 缓存，命中时直接返回已完成的 Future，不发请求；</li>
 *   <li>同一 URL 同时只有一个请求在拉取，其他调用等同一个 Future（single-flight）；</li>
 *   <li>过期后先返回旧值，后台刷新；只有从未拉取成功过才需要等待。</li>
 * </ul>
 */
public final class CardLoader {

    private record Entry(AgentCard card, long fetchedAtNanos) {
    }

    private final HttpClient http;
    private final long ttlNanos;
    private final Duration fetchTimeout;
    private final ConcurrentHashMap<URI, Entry> cache = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<URI, CompletableFuture<AgentCard>> inflight = new ConcurrentHashMap<>();

    public CardLoader(HttpClient http, Duration ttl, Duration fetchTimeout) {
        this.http = http;
        this.ttlNanos = ttl.toNanos();
        this.fetchTimeout = fetchTimeout;
    }

    /** 返回的 Card 对象在缓存有效期内是同一个实例，调用方可以据此复用基于它创建的对象。 */
    public CompletableFuture<AgentCard> load(URI uri) {
        Entry entry = cache.get(uri);
        if (entry != null) {
            if (System.nanoTime() - entry.fetchedAtNanos() > ttlNanos) {
                refresh(uri); // 过期：后台刷新，本次先用旧值
            }
            return CompletableFuture.completedFuture(entry.card());
        }
        return refresh(uri);
    }

    /** 让某个 Card 立即失效（例如收到对方版本变更的通知）。 */
    public void invalidate(URI uri) {
        cache.remove(uri);
    }

    private CompletableFuture<AgentCard> refresh(URI uri) {
        CompletableFuture<AgentCard> created = new CompletableFuture<>();
        CompletableFuture<AgentCard> existing = inflight.putIfAbsent(uri, created);
        if (existing != null) {
            return existing;
        }
        fetch(uri).whenComplete((card, error) -> {
            if (card != null) {
                Entry previous = cache.get(uri);
                // 内容没变时保留原实例，避免上层误以为 Card 变了而重建对象
                AgentCard kept = previous != null && previous.card().equals(card) ? previous.card() : card;
                cache.put(uri, new Entry(kept, System.nanoTime()));
                inflight.remove(uri, created);
                created.complete(kept);
            } else {
                // 刷新失败时保留旧值（TODO(实现)：记日志、计数，连续失败超过阈值再让旧值失效）
                inflight.remove(uri, created);
                created.completeExceptionally(error);
            }
        });
        return created;
    }

    private CompletableFuture<AgentCard> fetch(URI uri) {
        HttpRequest request = HttpRequest.newBuilder(uri).timeout(fetchTimeout).header("Accept", "application/json").GET().build();
        // TODO(实现)：边读边检查大小，而不是读完整个响应体再判断
        return http.sendAsync(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8)).thenApply(response -> {
            if (response.statusCode() != 200) {
                throw new CompletionException(AgentCards.invalid("CARD_FETCH_FAILED", "HTTP " + response.statusCode(), null));
            }
            boolean json = response.headers().firstValue("Content-Type")
                    .map(v -> v.toLowerCase().startsWith("application/json")).orElse(false);
            if (!json) {
                throw new CompletionException(AgentCards.invalid("CARD_FETCH_FAILED", "Card must be application/json", null));
            }
            return AgentCards.parse(response.body());
        });
    }
}
