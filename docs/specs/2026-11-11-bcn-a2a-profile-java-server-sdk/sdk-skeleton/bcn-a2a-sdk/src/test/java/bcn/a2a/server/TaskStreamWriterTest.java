package bcn.a2a.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import bcn.a2a.ext.BcnKeys;
import bcn.a2a.spi.ToolEvent;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.StreamingEventKind;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskState;
import org.a2aproject.sdk.spec.TaskStatus;
import org.a2aproject.sdk.spec.TaskStatusUpdateEvent;
import org.junit.jupiter.api.Test;

/** 流式输出状态机和唯一的扩展（工具观察）。 */
class TaskStreamWriterTest {

    /** 把事件收集到列表里的写入器。 */
    private static final class Capture extends TaskStreamWriter {
        final List<StreamingEventKind> events = new ArrayList<>();
        boolean closed;

        Capture(Set<String> activated, boolean engineMode) {
            super("ctx-1", activated, new CancelSignal(Instant.now().plusSeconds(60)), engineMode);
        }

        @Override
        protected void emit(StreamingEventKind event) {
            events.add(event);
        }

        @Override
        protected void onClosed() {
            closed = true;
        }
    }

    @Test
    void toolRecordIsEmittedWhenExtensionIsActivated() {
        Capture out = new Capture(Set.of(BcnKeys.EXT_TOOLS_V2), false);
        out.tool(ToolEvent.start("call-1", "search", Map.of("query", "西湖")));
        out.complete();

        assertEquals(3, out.events.size()); // Task 骨架、工具记录、COMPLETED
        assertInstanceOf(Task.class, out.events.get(0));
        Message message = ((TaskStatusUpdateEvent) out.events.get(1)).status().message();
        assertEquals(List.of(BcnKeys.EXT_TOOLS_V2), message.extensions());
        Map<?, ?> data = (Map<?, ?>) ((DataPart) message.parts().get(0)).data();
        assertEquals("call-1", data.get("toolCallId"));
        assertEquals("start", data.get("phase"));
        assertTrue(out.closed);
    }

    @Test
    void toolRecordIsDroppedWhenExtensionIsNotActivated() {
        Capture out = new Capture(Set.of(), false);
        out.tool(ToolEvent.start("call-1", "search", null));
        out.complete();
        assertEquals(2, out.events.size()); // 只有 Task 骨架和 COMPLETED
    }

    @Test
    void invalidToolRecordIsDroppedWithoutBreakingTheStream() {
        Capture out = new Capture(Set.of(BcnKeys.EXT_TOOLS_V2), false);
        out.tool(new ToolEvent("", "search", ToolEvent.Phase.START, null, null, null));
        out.text("正文");
        out.complete();
        assertEquals(3, out.events.size()); // Task 骨架、正文、COMPLETED
    }

    @Test
    void outputAfterTerminalIsRejected() {
        Capture out = new Capture(Set.of(), false);
        out.complete();
        assertThrows(IllegalStateException.class, () -> out.text("late"));
    }

    @Test
    void engineModeCannotEnterInputRequiredInThisPhase() {
        Capture out = new Capture(Set.of(), true);
        out.begin(Task.builder().id("task-1").contextId("ctx-1")
                .status(new TaskStatus(TaskState.TASK_STATE_WORKING)).build());
        assertThrows(IllegalStateException.class,
                () -> out.status(new TaskStatus(TaskState.TASK_STATE_INPUT_REQUIRED)));
    }
}
