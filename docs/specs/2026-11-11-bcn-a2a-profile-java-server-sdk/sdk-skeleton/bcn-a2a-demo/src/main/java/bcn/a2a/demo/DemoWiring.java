package bcn.a2a.demo;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

import bcn.a2a.server.A2aService;
import bcn.a2a.spi.A2aListener;
import bcn.a2a.spi.BcnAgent;

/**
 * 业务接入时自己写的装配代码。SDK 不做自动装配，这些都在业务手里，按应用自己的方式来。
 *
 * <p>在 SOFABoot 应用中，对应的写法大致如下（{@code @SofaService} 的具体写法以基座 5.14.1 的验证结果为准；
 * 接口类型使用 SDK 提供的 SOFA Triple 生成接口，接口名、版本、协议使用 {@code SofaPublishSpec} 中的固定值）：
 *
 * <pre>{@code
 * @Configuration
 * public class A2aConfig {
 *
 *     @Bean
 *     @SofaService(interfaceType = A2aSofaGrpc.Service.class,            // SDK 提供的生成接口（待定）
 *                  bindings = @SofaServiceBinding(bindingType = SofaPublishSpec.PROTOCOL))
 *     public A2aService a2aService(List<BcnAgent> agents, ExecutorService a2aExecutor, MyA2aMetrics metrics) {
 *         return A2aService.builder()
 *                 .agents(agents)                          // 容器里所有 @A2aAgent Bean，按 tenant 分发
 *                 .executor(a2aExecutor)                   // 业务自己的线程池
 *                 .taskDecorator(SofaTracerRunnable::new)  // 传递调用链上下文（示意）
 *                 .listener(metrics)                       // 业务自己的日志和指标
 *                 .build();
 *     }
 * }
 * }</pre>
 */
public final class DemoWiring {

    private DemoWiring() {
    }

    public static A2aService a2aService(List<BcnAgent> agents) {
        ExecutorService executor = new ThreadPoolExecutor(16, 64, 60, TimeUnit.SECONDS, new LinkedBlockingQueue<>(256));
        return A2aService.builder()
                .agents(agents)
                .executor(executor)
                .listener(new A2aListener() {
                    @Override
                    public void onEnd(CallInfo info, Outcome outcome, Throwable error) {
                        long ms = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - info.startNanos());
                        System.out.printf("a2a %s agent=%s outcome=%s cost=%dms%n", info.method(), info.agentId(), outcome, ms);
                    }
                })
                .build();
    }

    public static void main(String[] args) {
        A2aService service = a2aService(List.of(new OrderAgent(), new WritingAgent()));
        // 原生 gRPC 下可以直接 ServerBuilder.forPort(...).addService(service)；SOFA 下按上面的方式发布。
        System.out.println("A2A service ready: " + service.bindService().getServiceDescriptor().getName());
    }
}
