package a2a.agent.client.card;

import java.util.Map;
import java.util.Set;

import a2a.agent.client.transport.A2aTransport;
import a2a.agent.client.transport.CallContext;
import org.a2aproject.sdk.grpc.AgentCard;
import org.a2aproject.sdk.grpc.AgentExtension;
import org.a2aproject.sdk.grpc.AgentInterface;

/**
 * 校验 Card 并选出调用接口，规则与 BCS 一致（apps/bcs/.../bcs-a2a-http/src/card.rs）：
 * <ol>
 *   <li>Card 中 {@code required: true} 的扩展，本客户端必须都支持，否则拒绝；</li>
 *   <li>按 Card 中 supportedInterfaces 的顺序（即对方的偏好），选第一个满足条件的接口：
 *       本客户端装了对应的传输、协议版本为 1.0、地址通过该传输的校验；</li>
 *   <li>选定后不悄悄换成别的接口：调用失败就是失败，不自动改用 HTTP 或其他地址。</li>
 * </ol>
 */
public final class InterfaceSelector {

    private InterfaceSelector() {
    }

    public static SelectedInterface select(AgentCard card, Map<String, A2aTransport> transports, Set<String> supportedExtensions) {
        if (card.getSupportedInterfacesCount() == 0) {
            throw AgentCards.invalid("INTERFACE_MISSING", "AgentCard declares no interface", null);
        }
        for (AgentExtension extension : card.getCapabilities().getExtensionsList()) {
            if (extension.getRequired() && !supportedExtensions.contains(extension.getUri())) {
                throw AgentCards.invalid("REQUIRED_EXTENSION_UNSUPPORTED", "required extension: " + extension.getUri(), null);
            }
        }
        // TODO(内部)：Card 声明了鉴权要求（securityRequirements）且没有匿名选项时如何处理；内网调用目前不做应用层鉴权（D1）
        boolean rejected = false;
        for (AgentInterface candidate : card.getSupportedInterfacesList()) {
            A2aTransport transport = transports.get(candidate.getProtocolBinding());
            if (transport == null || !CallContext.PROTOCOL_VERSION.equals(candidate.getProtocolVersion())) {
                continue;
            }
            if (transport.accepts(candidate)) {
                return new SelectedInterface(card, candidate, transport);
            }
            rejected = true;
        }
        throw rejected
                ? AgentCards.invalid("ADDRESS_REJECTED", "no interface address passed validation", null)
                : AgentCards.invalid("INTERFACE_UNSUPPORTED", "no supported interface (binding + version 1.0)", null);
    }
}
