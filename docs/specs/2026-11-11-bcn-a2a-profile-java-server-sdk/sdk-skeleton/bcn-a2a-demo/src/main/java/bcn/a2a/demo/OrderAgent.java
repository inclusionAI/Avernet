package bcn.a2a.demo;

import bcn.a2a.spi.A2aAgent;
import bcn.a2a.spi.BcnAgent;
import bcn.a2a.spi.BcnCall;
import bcn.a2a.spi.BcnException;
import bcn.a2a.spi.Messages;
import org.a2aproject.sdk.spec.Message;

/**
 * 第一档示例：收到一条消息，返回一条完整回复。业务只需要写这一个方法。
 * 在 SOFABoot 应用中，它就是一个普通的 Spring Bean。
 */
@A2aAgent(id = "order-agent")
public class OrderAgent implements BcnAgent {

    @Override
    public Message onMessage(BcnCall call) {
        String question = call.text();
        if (question.isBlank()) {
            throw new BcnException(BcnException.Code.INVALID_INPUT, "请告诉我要查询的订单号");
        }
        // 业务自己约定的字段从 call.metadata() 读取，例如渠道；SDK 不解释这些字段。
        Object channel = call.metadata().getOrDefault("channel", "默认渠道");
        // 真实业务：调用订单服务
        return Messages.agentText(channel + "：订单 1001 已发货");
    }
}
