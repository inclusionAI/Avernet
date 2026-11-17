package bcn.a2a.server;

import bcn.a2a.spi.BcnCall;
import org.a2aproject.sdk.spec.Message;

/**
 * 出站检查：业务返回的内容在发给调用方之前必须符合 Profile。不符合时视为 Agent 的错误（INVALID_AGENT_RESPONSE），
 * 不把不合规的数据发出去。
 *
 * <p>流式输出的检查在 {@link TaskStreamWriter} 中按事件进行（首帧、状态机、终态之后不得再发）。
 */
final class OutboundGuard {

    private OutboundGuard() {
    }

    /**
     * 检查第一档的回复，并补齐 contextId。
     *
     * @return 可以直接发送的回复
     */
    static Message checkReply(Message reply, BcnCall call) {
        if (reply == null) {
            throw ProtocolErrors.badResponse("onMessage returned null");
        }
        if (reply.role() != Message.Role.ROLE_AGENT) {
            throw ProtocolErrors.badResponse("reply role must be ROLE_AGENT");
        }
        if (reply.taskId() != null) {
            throw ProtocolErrors.badResponse("a message-mode reply must not carry taskId");
        }
        if (reply.parts() == null || reply.parts().isEmpty()) {
            throw ProtocolErrors.badResponse("reply must have at least one part");
        }
        if (reply.contextId() != null && !reply.contextId().equals(call.contextId())) {
            throw ProtocolErrors.badResponse("reply contextId must equal the call contextId");
        }
        // TODO(实现)：
        //  1. 第一期只允许 TextPart（BCS 处理独立回复消息时只接受文本）；
        //  2. 回复总大小上限；
        //  3. message.extensions 中出现的平台扩展必须已激活；未激活扩展的保留键不得出现在 metadata 中；
        //  4. metadata 中不得出现 userId 等调用上下文字段（P-105）。
        return reply.contextId() != null ? reply : Message.builder(reply).contextId(call.contextId()).build();
    }
}
