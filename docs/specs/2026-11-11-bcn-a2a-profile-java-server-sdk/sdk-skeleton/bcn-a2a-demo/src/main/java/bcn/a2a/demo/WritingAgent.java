package bcn.a2a.demo;

import java.util.List;
import java.util.Map;

import bcn.a2a.spi.A2aAgent;
import bcn.a2a.spi.BcnAgent;
import bcn.a2a.spi.BcnCall;
import bcn.a2a.spi.BcnStream;
import bcn.a2a.spi.ToolEvent;

/**
 * 第二档示例：逐段输出，业务不保存任何状态。
 * 同一个实现同时服务 SendStreamingMessage（逐段推送）和 SendMessage（SDK 收集后一次返回）。
 */
@A2aAgent(id = "writing-agent")
public class WritingAgent implements BcnAgent {

    @Override
    public void onStream(BcnCall call, BcnStream out) {
        out.tool(ToolEvent.start("call-1", "search_material", Map.of("query", call.text())));
        out.tool(ToolEvent.end("call-1", "search_material", Map.of("hits", 3), false));
        // 真实业务：大模型流式输出，每收到一段就调用 out.text
        for (String delta : List.of("杭州", "两日游", "：第一天西湖，", "第二天灵隐寺。")) {
            if (out.isCancelled()) {
                return; // 调用方取消或超时，尽快返回
            }
            out.text(delta);
        }
        out.complete(); // 不写也可以：方法正常返回时 SDK 自动完成
    }
}
