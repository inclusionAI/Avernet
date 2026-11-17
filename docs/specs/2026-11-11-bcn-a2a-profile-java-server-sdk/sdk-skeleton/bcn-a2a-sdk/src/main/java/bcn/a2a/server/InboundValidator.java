package bcn.a2a.server;

import org.a2aproject.sdk.spec.ContentTypeNotSupportedError;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.MessageSendParams;
import org.a2aproject.sdk.spec.Part;
import org.a2aproject.sdk.spec.TextPart;

/**
 * 入站校验：在调用业务之前检查请求是否符合 Profile。原则是“协议结构严格，不认识的数据保留并忽略”。
 * 业务字段（非保留键）不做任何校验，只透传。
 */
final class InboundValidator {

    private InboundValidator() {
    }

    static void validate(MessageSendParams params, ExtensionNegotiator.Negotiation negotiation) {
        Message message = params.message();
        if (message == null) {
            throw ProtocolErrors.invalid("MESSAGE_REQUIRED", "message is required");
        }
        if (message.messageId() == null || message.messageId().isBlank()) {
            throw ProtocolErrors.invalid("MESSAGE_ID_REQUIRED", "message.messageId is required");
        }
        if (message.role() != Message.Role.ROLE_USER) {
            throw ProtocolErrors.invalid("INVALID_ROLE", "message.role must be ROLE_USER");
        }
        if (message.parts() == null || message.parts().isEmpty()) {
            throw ProtocolErrors.invalid("EMPTY_MESSAGE", "message.parts must not be empty");
        }
        for (Part<?> part : message.parts()) {
            // 第一期只接受文本；支持多模态后按 Agent 声明的输入类型放开。
            if (!(part instanceof TextPart)) {
                throw new ContentTypeNotSupportedError(null, "only text parts are supported", null);
            }
        }
        // TODO(实现)：
        //  1. 文本总长度上限；
        //  2. configuration.acceptedOutputModes 不为空且不包含 text/plain 时，返回 ContentTypeNotSupportedError；
        //  3. message.extensions 标记了某个平台扩展、但该扩展未激活（negotiation.activated() 中没有）时：
        //     带了该扩展的数据就拒绝（不能靠消息标记绕过协商），只有标记没有数据就忽略；
        //  4. metadata 中未激活扩展的保留键：保留并忽略，不报错；
        //  5. referenceTaskIds 只透传，不解析，不做存在性检查。
    }
}
