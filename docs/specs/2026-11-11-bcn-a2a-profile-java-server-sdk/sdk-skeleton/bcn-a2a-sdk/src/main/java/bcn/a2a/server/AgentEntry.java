package bcn.a2a.server;

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

    /**
     * 参与协商的扩展。本期只有工具观察：它的数据放在 Task 的状态消息中，所以只有流式档位支持。
     * 所有扩展都是可选的（required:false），未激活时 Agent 照常工作。
     */
    Set<String> supportedExtensions() {
        return mode == Mode.STREAM ? Set.of(BcnKeys.EXT_TOOLS_V2) : Set.of();
    }
}
