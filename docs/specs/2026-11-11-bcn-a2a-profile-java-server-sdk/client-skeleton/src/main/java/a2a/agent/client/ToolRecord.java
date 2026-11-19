package a2a.agent.client;

import java.util.Map;

/**
 * 一条工具调用记录（toolcall 扩展，Payload 规范第 8 节）。过程信息，可能丢失，不能据此判断任务成败。
 *
 * @param toolCallId   同一次调用的 start 和 result 使用相同的 ID
 * @param name         工具名
 * @param phase        阶段
 * @param args         调用参数，只在 START 中可能出现，否则为 null
 * @param result       调用结果（任意 JSON 转成的 Map / List / 基本类型），只在 RESULT 中可能出现，否则为 null
 * @param isError      工具是否失败，只在 RESULT 中可能出现，否则为 null
 * @param partMetadata 这条记录所在 Part 的 metadata（例如来源 server_id），没有时为空 Map
 */
public record ToolRecord(String toolCallId, String name, Phase phase, Map<String, Object> args, Object result,
                         Boolean isError, Map<String, Object> partMetadata) {

    public enum Phase { START, RESULT }
}
