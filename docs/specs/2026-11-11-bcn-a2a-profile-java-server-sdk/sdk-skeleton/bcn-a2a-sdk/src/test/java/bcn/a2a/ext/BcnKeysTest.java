package bcn.a2a.ext;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.LinkedHashMap;
import java.util.Map;

import org.junit.jupiter.api.Test;

class BcnKeysTest {

    @Test
    void reservedKeys() {
        assertTrue(BcnKeys.isReserved("bcn"));
        assertTrue(BcnKeys.isReserved("bcn_trace"));
        assertTrue(BcnKeys.isReserved("urn:example:ext:v1"));
        assertFalse(BcnKeys.isReserved("bcnx"));
        assertFalse(BcnKeys.isReserved("channel"));
        assertFalse(BcnKeys.isReserved("order.channel"));
    }

    @Test
    void businessViewDropsReservedKeys() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("channel", "app");
        metadata.put("bcn_trace", "t-1");
        metadata.put("https://example.com/ext/v1", Map.of());
        assertEquals(Map.of("channel", "app"), BcnKeys.businessView(metadata));
    }
}
