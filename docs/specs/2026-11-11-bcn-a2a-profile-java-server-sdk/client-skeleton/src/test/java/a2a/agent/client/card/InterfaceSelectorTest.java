package a2a.agent.client.card;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.Map;
import java.util.Set;

import a2a.agent.client.A2aCallException;
import a2a.agent.client.transport.A2aTransport;
import a2a.agent.client.transport.grpc.DirectGrpcTransport;
import a2a.agent.client.transport.grpc.MeshGrpcTransport;
import io.grpc.ManagedChannel;
import io.grpc.inprocess.InProcessChannelBuilder;
import org.a2aproject.sdk.grpc.AgentCard;
import org.junit.jupiter.api.Test;

class InterfaceSelectorTest {

    private static AgentCard card(String interfaces, String extensions) {
        return AgentCards.parse("""
                {"name":"n","description":"d","version":"1","supportedInterfaces":[%s],
                 "capabilities":{"streaming":true,"extensions":[%s]},"defaultInputModes":[],"defaultOutputModes":[],"skills":[]}
                """.formatted(interfaces, extensions));
    }

    @Test
    void picksFirstInterfaceWithInstalledTransportAndVersion10() {
        AgentCard card = card("""
                {"url":"http://a","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"},
                {"url":"grpc://b:1","protocolBinding":"GRPC","protocolVersion":"0.3"},
                {"url":"grpc://c:1","protocolBinding":"GRPC","protocolVersion":"1.0","tenant":"t"}
                """, "");
        Map<String, A2aTransport> transports = Map.of(DirectGrpcTransport.BINDING, DirectGrpcTransport.create());
        SelectedInterface selected = InterfaceSelector.select(card, transports, Set.of());
        assertEquals("grpc://c:1", selected.agentInterface().getUrl());
        assertEquals("t", selected.tenant());
    }

    @Test
    void rejectsUnknownRequiredExtension() {
        AgentCard card = card("{\"url\":\"grpc://c:1\",\"protocolBinding\":\"GRPC\",\"protocolVersion\":\"1.0\"}",
                "{\"uri\":\"urn:example:unknown:v1\",\"required\":true}");
        A2aCallException e = assertThrows(A2aCallException.class, () -> InterfaceSelector.select(card,
                Map.of(DirectGrpcTransport.BINDING, DirectGrpcTransport.create()), Set.of()));
        assertEquals(A2aCallException.Kind.CARD_INVALID, e.kind());
        assertEquals("REQUIRED_EXTENSION_UNSUPPORTED", e.reason());
    }

    @Test
    void rejectsMalformedSofaGrpcAddress() {
        ManagedChannel channel = InProcessChannelBuilder.forName("unused").build();
        try {
            AgentCard card = card("{\"url\":\"sofa-grpc://only-app\",\"protocolBinding\":\"SOFA-GRPC\",\"protocolVersion\":\"1.0\"}", "");
            A2aCallException e = assertThrows(A2aCallException.class, () -> InterfaceSelector.select(card,
                    Map.of(MeshGrpcTransport.BINDING, MeshGrpcTransport.using(channel)), Set.of()));
            assertEquals("ADDRESS_REJECTED", e.reason());
        } finally {
            channel.shutdownNow();
        }
    }
}
