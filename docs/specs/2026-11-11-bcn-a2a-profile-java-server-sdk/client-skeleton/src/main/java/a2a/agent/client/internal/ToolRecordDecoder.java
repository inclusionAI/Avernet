package a2a.agent.client.internal;

import java.util.Map;

import a2a.agent.client.ToolRecord;
import com.google.protobuf.Value;
import org.a2aproject.sdk.grpc.Part;

/**
 * 解码一条工具记录（Payload 规范 8.4）。不合规的记录返回 null，由调用方丢弃，不影响整次调用（PL-54）。
 */
final class ToolRecordDecoder {

    private ToolRecordDecoder() {
    }

    static ToolRecord decode(Part part) {
        if (part.getContentCase() != Part.ContentCase.DATA || !part.getData().hasStructValue()) {
            return null;
        }
        Map<String, Value> fields = part.getData().getStructValue().getFieldsMap();
        String toolCallId = string(fields.get("toolCallId"));
        String name = string(fields.get("name"));
        ToolRecord.Phase phase = phase(string(fields.get("phase")));
        if (toolCallId == null || toolCallId.isEmpty() || name == null || name.isEmpty() || phase == null) {
            return null;
        }
        Value args = fields.get("args");
        Value result = fields.get("result");
        Value isError = fields.get("isError");
        return new ToolRecord(toolCallId, name, phase,
                args != null && args.hasStructValue() ? Protos.toJavaMap(args.getStructValue()) : null,
                result != null ? Protos.toJava(result) : null,
                isError != null && isError.getKindCase() == Value.KindCase.BOOL_VALUE ? isError.getBoolValue() : null,
                part.hasMetadata() ? Protos.toJavaMap(part.getMetadata()) : Map.of());
    }

    private static String string(Value v) {
        return v != null && v.getKindCase() == Value.KindCase.STRING_VALUE ? v.getStringValue() : null;
    }

    private static ToolRecord.Phase phase(String value) {
        if ("start".equals(value)) {
            return ToolRecord.Phase.START;
        }
        if ("result".equals(value)) {
            return ToolRecord.Phase.RESULT;
        }
        return null;
    }
}
