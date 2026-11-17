package bcn.a2a.spi;

import java.util.Map;

/**
 * 一条工具观察记录（tools/v2）。只用于展示，不表示审批，也不改变 Task 状态（P-185）。
 *
 * @param toolCallId 工具调用 ID，必填；同一次工具调用的 start 和 end 使用相同的 ID
 * @param name       工具名，必填
 * @param phase      阶段，必填
 * @param args       参数，可为 null；不得包含凭据和 userId
 * @param result     结果，可为 null
 * @param isError    是否失败，可为 null；工具失败不等于 Task 失败
 */
public record ToolEvent(String toolCallId, String name, Phase phase,
                        Map<String, Object> args, Object result, Boolean isError) {

    /** 线上取值为小写：start / end。 */
    public enum Phase { START, END }

    public static ToolEvent start(String toolCallId, String name, Map<String, Object> args) {
        return new ToolEvent(toolCallId, name, Phase.START, args, null, null);
    }

    public static ToolEvent end(String toolCallId, String name, Object result, boolean isError) {
        return new ToolEvent(toolCallId, name, Phase.END, null, result, isError);
    }
}
