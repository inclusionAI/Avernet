package bcn.a2a.demo;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Map;

import bcn.a2a.server.A2aService;
import bcn.a2a.spi.A2aAgent;
import bcn.a2a.spi.BcnAgent;
import bcn.a2a.spi.BcnCall;
import bcn.a2a.spi.BcnStream;
import bcn.a2a.spi.Messages;
import bcn.a2a.test.A2aAgentTester;
import io.grpc.Status;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.StreamingEventKind;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskArtifactUpdateEvent;
import org.a2aproject.sdk.spec.TaskState;
import org.a2aproject.sdk.spec.TaskStatusUpdateEvent;
import org.junit.jupiter.api.Test;

/** 用测试运行器验证骨架的主流程：分发、两种档位、流式事件顺序、错误映射、启动校验。 */
class DemoAgentsTest {

    private final A2aAgentTester tester = A2aAgentTester.of(new OrderAgent(), new WritingAgent());

    @Test
    void messageAgentRepliesWithOneMessage() {
        assertEquals("匿名用户，订单 1001 已发货", tester.send("order-agent", "查一下订单 1001").text());
    }

    @Test
    void callerContextIsParsedWithoutNegotiationAndHiddenFromBusinessMetadata() {
        @A2aAgent(id = "echo")
        class Echo implements BcnAgent {
            @Override
            public Message onMessage(BcnCall call) {
                String user = call.caller().map(c -> c.userId()).orElse("-");
                return Messages.agentText(user + "|" + String.join(",", call.metadata().keySet()));
            }
        }
        A2aAgentTester echo = A2aAgentTester.of(new Echo());
        Map<String, Object> metadata = Map.of(
                "bcn_caller", Map.of("userId", "u-1", "source", Map.of("platform", "bcn")),
                "channel", "app");
        assertEquals("u-1|channel", echo.send("echo", "hi", metadata).text());
    }

    @Test
    void messageAgentStreamsExactlyOneMessage() {
        List<StreamingEventKind> events = tester.stream("order-agent", "查一下订单 1001");
        assertEquals(1, events.size());
        assertInstanceOf(Message.class, events.get(0));
    }

    @Test
    void businessRejectionMapsToInvalidArgument() {
        assertEquals(Status.Code.INVALID_ARGUMENT, tester.send("order-agent", " ").code());
    }

    @Test
    void unknownTenantIsRejected() {
        assertEquals(Status.Code.INVALID_ARGUMENT, tester.send("no-such-agent", "hi").code());
    }

    @Test
    void streamAgentStartsWithTaskAndEndsWithExplicitTerminal() {
        List<StreamingEventKind> events = tester.stream("writing-agent", "写一份杭州两日游");
        assertInstanceOf(Task.class, events.get(0));
        assertTrue(events.stream().anyMatch(TaskArtifactUpdateEvent.class::isInstance));
        TaskStatusUpdateEvent last = assertInstanceOf(TaskStatusUpdateEvent.class, events.get(events.size() - 1));
        assertEquals(TaskState.TASK_STATE_COMPLETED, last.status().state());
    }

    @Test
    void streamAgentAlsoServesNonStreamingCall() {
        assertEquals("杭州两日游：第一天西湖，第二天灵隐寺。", tester.send("writing-agent", "写一份杭州两日游").text());
    }

    @Test
    void publishesTheStandardA2aGrpcService() {
        A2aService service = DemoWiring.a2aService(List.of(new OrderAgent(), new WritingAgent()));
        assertEquals("lf.a2a.v1.A2AService", service.bindService().getServiceDescriptor().getName());
    }

    @Test
    void agentMustOverrideExactlyOneMethod() {
        @A2aAgent(id = "both")
        class Both implements BcnAgent {
            @Override
            public Message onMessage(BcnCall call) {
                return null;
            }

            @Override
            public void onStream(BcnCall call, BcnStream out) {
            }
        }
        assertThrows(IllegalStateException.class,
                () -> A2aService.builder().agent(new Both()).executor(Runnable::run).build());
    }
}
