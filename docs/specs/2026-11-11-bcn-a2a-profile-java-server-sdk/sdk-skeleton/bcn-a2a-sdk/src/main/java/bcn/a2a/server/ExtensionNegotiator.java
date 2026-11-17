package bcn.a2a.server;

import java.util.LinkedHashSet;
import java.util.Set;

/**
 * 扩展协商：激活集合 = 调用方请求的 ∩ 该 Agent 支持的。
 *
 * <ul>
 *   <li>请求了但不支持的扩展：忽略，不报错，也不在响应头中确认（A2A：不得回退到其他版本）。</li>
 *   <li>只在消息的 extensions 字段里写了 URI、没有在请求头中请求的，不算激活。</li>
 *   <li>本期所有扩展都是可选的，不存在“必须激活”的情况，因此不会返回 ExtensionSupportRequiredError。</li>
 * </ul>
 */
final class ExtensionNegotiator {

    private ExtensionNegotiator() {
    }

    /** @return 本次调用激活的扩展 URI，不可变 */
    static Set<String> negotiate(Set<String> requested, AgentEntry entry) {
        Set<String> activated = new LinkedHashSet<>();
        Set<String> supported = entry.supportedExtensions();
        for (String uri : requested) {
            if (supported.contains(uri)) {
                activated.add(uri);
            }
        }
        return Set.copyOf(activated);
    }
}
