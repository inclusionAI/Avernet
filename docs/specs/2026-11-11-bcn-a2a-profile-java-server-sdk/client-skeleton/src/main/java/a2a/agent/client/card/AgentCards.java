package a2a.agent.client.card;

import a2a.agent.client.A2aCallException;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.protobuf.util.JsonFormat;
import org.a2aproject.sdk.grpc.AgentCard;

/**
 * AgentCard 的解析。Card 的 JSON 就是 protobuf AgentCard 的 ProtoJSON，直接解析成 protobuf 对象，
 * 后续选接口、读能力都在 protobuf 上进行，不再转换成 a2a-java 的 POJO。
 */
public final class AgentCards {

    /** 与 BCS 拉取 Card 时的上限一致。 */
    public static final int MAX_CARD_BYTES = 1 << 20;

    private static final JsonFormat.Parser PARSER = JsonFormat.parser().ignoringUnknownFields();

    private AgentCards() {
    }

    public static AgentCard parse(String json) {
        if (json == null || json.isBlank()) {
            throw invalid("CARD_EMPTY", "empty AgentCard", null);
        }
        if (json.length() > MAX_CARD_BYTES) {
            throw invalid("CARD_TOO_LARGE", "AgentCard exceeds " + MAX_CARD_BYTES + " bytes", null);
        }
        try {
            AgentCard.Builder builder = AgentCard.newBuilder();
            PARSER.merge(json, builder);
            return builder.build();
        } catch (InvalidProtocolBufferException | RuntimeException e) {
            throw invalid("CARD_MALFORMED", "cannot parse AgentCard", e);
        }
    }

    static A2aCallException invalid(String reason, String message, Throwable cause) {
        return new A2aCallException(A2aCallException.Kind.CARD_INVALID, reason, null, message, cause);
    }
}
