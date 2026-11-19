package a2a.agent.client.internal;

import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

/**
 * messageId 生成。
 *
 * <p><b>性能</b>：{@link UUID#randomUUID()} 使用 SecureRandom，高并发下有锁竞争和熵耗尽的风险；
 * messageId 只需要唯一、不需要不可预测，所以用 ThreadLocalRandom 生成同样格式的 UUID v4。
 */
public final class Ids {

    private Ids() {
    }

    public static String newMessageId() {
        ThreadLocalRandom random = ThreadLocalRandom.current();
        long msb = (random.nextLong() & 0xFFFFFFFFFFFF0FFFL) | 0x0000000000004000L; // version 4
        long lsb = (random.nextLong() & 0x3FFFFFFFFFFFFFFFL) | 0x8000000000000000L; // IETF variant
        return new UUID(msb, lsb).toString();
    }
}
