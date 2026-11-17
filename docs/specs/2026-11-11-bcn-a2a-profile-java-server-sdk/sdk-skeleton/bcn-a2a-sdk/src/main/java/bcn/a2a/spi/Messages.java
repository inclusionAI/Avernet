package bcn.a2a.spi;

import java.util.List;
import java.util.stream.Collectors;

import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.Part;
import org.a2aproject.sdk.spec.TextPart;

/** 构造和读取消息的便捷方法。 */
public final class Messages {

    private Messages() {
    }

    /** 构造一条只含一段文本的 Agent 回复。messageId 自动生成；contextId 由 SDK 填写。 */
    public static Message agentText(String text) {
        return Message.builder()
                .role(Message.Role.ROLE_AGENT)
                .parts(List.<Part<?>>of(new TextPart(text)))
                .build();
    }

    /** 取出消息中所有文本 Part，按顺序以换行拼接。没有文本时返回空字符串。 */
    public static String textOf(Message message) {
        if (message == null || message.parts() == null) {
            return "";
        }
        return message.parts().stream()
                .filter(TextPart.class::isInstance)
                .map(part -> ((TextPart) part).text())
                .collect(Collectors.joining("\n"));
    }
}
