package bcn.a2a.server;

import java.util.LinkedHashSet;
import java.util.Optional;
import java.util.Set;

import bcn.a2a.ext.BcnKeys;
import bcn.a2a.spi.A2aAgent;
import bcn.a2a.spi.BcnAgent;
import bcn.a2a.spi.TaskEngine;

/**
 * 注册表中的一个 Agent：实例、声明和启动时推断出的档位。
 *
 * @param engine 引擎模式时为业务实现的 {@link TaskEngine}，否则为 null
 */
record AgentEntry(String id, A2aAgent meta, BcnAgent agent, Mode mode, TaskEngine engine) {

    enum Mode {
        /** 第一档：覆盖了 onMessage。 */
        MESSAGE,
        /** 第二、三档：覆盖了 onStream。 */
        STREAM
    }

    Optional<TaskEngine> taskEngine() {
        return Optional.ofNullable(engine);
    }

    boolean streaming() {
        return mode == Mode.STREAM;
    }

    /**
     * SDK 为这个 Agent 支持的平台扩展：
     * 调用上下文总是支持；工具观察需要 Task，只有流式档位支持。
     * TODO(hitl)：引擎模式支持 hitl/v1 后在这里加入。
     */
    Set<String> platformExtensions() {
        Set<String> uris = new LinkedHashSet<>();
        uris.add(BcnKeys.EXT_CALLER);
        if (streaming()) {
            uris.add(BcnKeys.EXT_TOOLS_V2);
        }
        return uris;
    }

    /** 参与协商的全部扩展：平台扩展 + 注解中声明的业务扩展。 */
    Set<String> supportedExtensions() {
        Set<String> uris = new LinkedHashSet<>(platformExtensions());
        uris.addAll(Set.of(meta.extensions()));
        return uris;
    }

    /** 调用方必须激活的扩展（只可能是业务扩展；平台扩展一律 required:false）。 */
    Set<String> requiredExtensions() {
        return Set.of(meta.requiredExtensions());
    }
}
