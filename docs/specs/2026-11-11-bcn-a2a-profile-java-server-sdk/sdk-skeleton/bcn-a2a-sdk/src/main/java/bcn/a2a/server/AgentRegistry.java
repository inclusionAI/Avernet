package bcn.a2a.server;

import java.lang.reflect.Proxy;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

import bcn.a2a.spi.A2aAgent;
import bcn.a2a.spi.BcnAgent;
import bcn.a2a.spi.BcnCall;
import bcn.a2a.spi.BcnStream;
import bcn.a2a.spi.TaskEngine;

/**
 * 应用内的 Agent 注册表：启动时校验每个 Agent 的声明和档位，运行时按请求 tenant 查找。
 *
 * <p>启动校验失败直接抛 {@link IllegalStateException}，让应用启动失败，而不是带着错误配置上线。
 */
final class AgentRegistry {

    private static final Pattern ID = Pattern.compile("[a-z0-9]+(-[a-z0-9]+)*");

    private final Map<String, AgentEntry> byId;

    private AgentRegistry(Map<String, AgentEntry> byId) {
        this.byId = byId;
    }

    static AgentRegistry of(Collection<? extends BcnAgent> agents) {
        if (agents == null || agents.isEmpty()) {
            throw new IllegalStateException("at least one BcnAgent is required");
        }
        Map<String, AgentEntry> byId = new LinkedHashMap<>();
        for (BcnAgent agent : agents) {
            AgentEntry entry = inspect(agent);
            if (byId.putIfAbsent(entry.id(), entry) != null) {
                throw new IllegalStateException("duplicate @A2aAgent id: " + entry.id());
            }
        }
        return new AgentRegistry(byId);
    }

    /**
     * 按请求 tenant 查找 Agent。
     * tenant 为空时：应用只有一个 Agent 就用它（方便单 Agent 应用和本地测试）；否则拒绝。
     */
    AgentEntry resolve(String tenant) {
        if (tenant == null || tenant.isEmpty()) {
            if (byId.size() == 1) {
                return byId.values().iterator().next();
            }
            throw ProtocolErrors.invalid("AGENT_REQUIRED", "tenant is required when the service hosts several agents");
        }
        AgentEntry entry = byId.get(tenant);
        if (entry == null) {
            throw ProtocolErrors.invalid("AGENT_NOT_FOUND", "no agent for tenant " + tenant);
        }
        return entry;
    }

    Collection<AgentEntry> all() {
        return List.copyOf(byId.values());
    }

    // ---------------------------------------------------------------- 启动校验

    private static AgentEntry inspect(BcnAgent agent) {
        Class<?> type = userClass(agent.getClass());
        if (Proxy.isProxyClass(type)) {
            throw new IllegalStateException("BcnAgent is wrapped by a JDK dynamic proxy; use class-based proxies "
                    + "(proxyTargetClass=true) so that @A2aAgent can be read: " + type.getName());
        }
        A2aAgent meta = type.getAnnotation(A2aAgent.class);
        if (meta == null) {
            throw new IllegalStateException("missing @A2aAgent on " + type.getName());
        }
        if (!ID.matcher(meta.id()).matches()) {
            throw new IllegalStateException("@A2aAgent id must match " + ID.pattern() + ": " + meta.id());
        }
        boolean message = overrides(type, "onMessage", BcnCall.class);
        boolean stream = overrides(type, "onStream", BcnCall.class, BcnStream.class);
        if (message == stream) {
            throw new IllegalStateException("agent " + meta.id() + " must override exactly one of onMessage / onStream");
        }
        TaskEngine engine = agent instanceof TaskEngine e ? e : null;
        if (engine != null && !stream) {
            throw new IllegalStateException("TaskEngine requires onStream: " + meta.id());
        }
        return new AgentEntry(meta.id(), meta, agent, stream ? AgentEntry.Mode.STREAM : AgentEntry.Mode.MESSAGE, engine);
    }

    /** 去掉 CGLIB 等字节码代理生成的子类（类名含 "$$"），回到业务自己写的类。 */
    private static Class<?> userClass(Class<?> type) {
        Class<?> current = type;
        while (current.getName().contains("$$") && current.getSuperclass() != null) {
            current = current.getSuperclass();
        }
        return current;
    }

    /** 业务类（含其父类）是否覆盖了 BcnAgent 的某个 default 方法。 */
    private static boolean overrides(Class<?> type, String name, Class<?>... params) {
        for (Class<?> c = type; c != null && c != Object.class; c = c.getSuperclass()) {
            try {
                c.getDeclaredMethod(name, params);
                return true;
            } catch (NoSuchMethodException ignored) {
                // 继续向父类查找
            }
        }
        return false;
    }
}
