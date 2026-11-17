/**
 * SOFA 相关的约定和胶水代码。SDK 不做 SOFABoot 自动装配，服务发布、线程池、调用链、指标都由业务按自己的方式完成；
 * 这里只放对所有应用都相同、又影响协议正确性的部分。
 *
 * <h2>已有</h2>
 * <ul>
 *   <li>{@link bcn.a2a.sofa.SofaPublishSpec}：发布时必须使用的固定值（接口名、版本、协议）。</li>
 * </ul>
 *
 * <h2>TODO(SOFA)，需要在基座 5.14.1 上验证后实现</h2>
 * <ol>
 *   <li><b>生成代码</b>：SOFA Triple 根据 a2a.proto 生成的接口（你现有代码里的 {@code A2aSofaGrpc}）只能有一份，
 *       由 SDK 提供，不让每个应用自己生成，否则包名、服务名可能不一致。放在 provided 依赖或共享 jar 中。</li>
 *   <li><b>代理调用路径</b>：SOFA 通过生成接口代理调用服务时，{@code A2aService} 需要实现该生成接口。
 *       同步方法在 RPC 线程上调用 {@code RequestPipeline}，用阻塞式的结果收集器等待结果，
 *       以保留 SOFA 的治理上下文；不要再把等待放进业务线程池，避免嵌套提交导致线程池自锁。</li>
 *   <li><b>请求头</b>：代理路径不经过 gRPC 拦截器时，A2A-Extensions / A2A-Version 从哪里读、
 *       激活结果写到哪里。读不到时按“未请求任何扩展”处理，不能抛 NPE（CallHeaders 已按此处理）。</li>
 *   <li><b>异常</b>：经 SOFA 代理后异常可能被包装，{@code ErrorMapper} 需要沿 cause 链还原 A2A 错误；
 *       并验证 {@code google.rpc.ErrorInfo} 能完整穿过 SOFARPC Triple 和 MOSN。</li>
 *   <li><b>其他验证项</b>：服务端流、deadline 传递、调用方取消能否传到服务端、starter 能否以编程方式发布、
 *       发布流程是否要求在 XML 或注解里显式声明服务。</li>
 * </ol>
 */
package bcn.a2a.sofa;
