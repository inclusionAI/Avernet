package bcn.a2a.spi;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Inherited;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 声明一个 Agent，用于按请求 tenant 分发。标注在 {@link BcnAgent} 的实现类上。
 *
 * <p>AgentCard 由平台方注册，SDK 不生成 Card。平台注册的 Card 中，接口项的 tenant 应等于 {@link #id()}；
 * 流式档位的 Agent 可以声明工具观察扩展，SDK 会自动参与协商。
 *
 * <p>带 {@code @Inherited}：Spring 用 CGLIB 生成的代理子类也能读到这个注解。
 * 如果 Bean 被 JDK 动态代理包装（proxyTargetClass=false），SDK 读不到注解，启动时会报错并提示原因。
 */
@Documented
@Inherited
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.TYPE)
public @interface A2aAgent {

    /**
     * Agent 在应用内的唯一标识，即请求中的 tenant（也是 Card 接口项中的 tenant）。
     * 只允许小写字母、数字和连字符，注册后不要修改。
     */
    String id();
}
