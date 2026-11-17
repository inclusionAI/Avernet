package bcn.a2a.server;

import java.util.LinkedHashSet;
import java.util.Set;

/**
 * 扩展协商：激活集合 = 调用方请求的 ∩ 该 Agent 支持的。
 *
 * <ul>
 *   <li>请求了但不支持的扩展：忽略，不报错，也不在响应头中确认（A2A：不得回退到其他版本）。</li>
 *   <li>Agent 要求必须激活、但调用方没有请求的扩展：记入 missingRequired，由调用方返回 ExtensionSupportRequiredError。</li>
 *   <li>只在消息的 extensions 字段里写了 URI、没有在请求头中请求的，不算激活（入站校验负责拒绝借此绕过协商的数据）。</li>
 * </ul>
 */
final class ExtensionNegotiator {

    record Negotiation(Set<String> activated, Set<String> missingRequired) {
    }

    private ExtensionNegotiator() {
    }

    static Negotiation negotiate(Set<String> requested, AgentEntry entry) {
        Set<String> activated = new LinkedHashSet<>();
        Set<String> supported = entry.supportedExtensions();
        for (String uri : requested) {
            if (supported.contains(uri)) {
                activated.add(uri);
            }
        }
        Set<String> missing = new LinkedHashSet<>(entry.requiredExtensions());
        missing.removeAll(requested);
        return new Negotiation(Set.copyOf(activated), Set.copyOf(missing));
    }
}
