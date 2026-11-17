package bcn.a2a.spi;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Inherited;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 声明一个 Agent，用于按请求 tenant 分发和扩展协商。标注在 {@link BcnAgent} 的实现类上。
 *
 * <p>AgentCard 由平台方注册，SDK 不生成 Card。平台注册的 Card 应与这里一致：
 * 接口项的 tenant 等于 {@link #id()}，声明的业务扩展等于 {@link #extensions()}。
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

    /**
     * 业务扩展的 URI。SDK 只让它们参与协商，数据原样透传，不解释。
     * 平台扩展（调用上下文、工具观察等）由 SDK 按档位自动支持，不要写在这里。
     */
    String[] extensions() default {};

    /** 业务扩展中要求调用方必须激活的部分（Card 中应声明为 required: true）。必须是 {@link #extensions()} 的子集。 */
    String[] requiredExtensions() default {};
}
