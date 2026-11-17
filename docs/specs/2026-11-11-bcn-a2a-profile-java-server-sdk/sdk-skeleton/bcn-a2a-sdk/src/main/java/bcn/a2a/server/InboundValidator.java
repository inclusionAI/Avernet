package bcn.a2a.server;

import java.util.Set;

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

    /** @param activated 本次调用激活的扩展（用于第 3 条 TODO） */
    static void validate(MessageSendParams params, Set<String> activated) {
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
        //  3. message.extensions 中标记了未激活的扩展（activated 中没有）：只忽略标记，不能因此认为扩展已激活；
        //  4. metadata 中的保留键：bcn_caller 由 CallerContextCodec 解析；其余 bcn、bcn_*、URI 键保留并忽略，不报错；
        //  5. referenceTaskIds 只透传，不解析，不做存在性检查。
    }
}
