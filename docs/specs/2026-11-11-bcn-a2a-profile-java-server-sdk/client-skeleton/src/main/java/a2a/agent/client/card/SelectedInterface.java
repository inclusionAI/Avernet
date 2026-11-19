package a2a.agent.client.card;

import a2a.agent.client.transport.A2aTransport;
import org.a2aproject.sdk.grpc.AgentCard;
import org.a2aproject.sdk.grpc.AgentExtension;
import org.a2aproject.sdk.grpc.AgentInterface;

/** 选定的接口：Card、接口项和对应的传输。 */
public record SelectedInterface(AgentCard card, AgentInterface agentInterface, A2aTransport transport) {

    public String binding() {
        return agentInterface.getProtocolBinding();
    }

    /** 请求中的 tenant，取自接口项；没有时为空字符串。 */
    public String tenant() {
        return agentInterface.getTenant();
    }

    /** Card 是否声明支持流式。不支持时客户端用一元调用模拟流式回调。 */
    public boolean streaming() {
        return card.getCapabilities().getStreaming();
    }

    public boolean declaresExtension(String uri) {
        for (AgentExtension extension : card.getCapabilities().getExtensionsList()) {
            if (extension.getUri().equals(uri)) {
                return true;
            }
        }
        return false;
    }
}
