# A2A gRPC / MOSN / SOFARPC Triple 联调

本阶段沿用 A2A 1.0，增加 gRPC binding。HTTP 阶段的设计与验收保留在
`plan.md`、`acceptance-checklist.md`；gRPC 结果单独记录，不能混用。

## 链路与依赖

```text
BCS shared A2A driver
  -> tonic generated A2A client
  -> local MOSN egress 50002
  -> registry-discovered peer MOSN ingress 51051
  -> Harness SOFARPC Triple 50001
  -> official Java A2A handler / shared persistent Task runtime
  -> Harness engine
```

本机验证使用同一个专用 MOSN 进程承担两个方向，经过真实服务发布、订阅与
寻址；尚未验证两台主机上的部署。AgentCard 仍由 SOFABoot HTTP 发布。文本、
工具、审批和 Task 生命周期共用 HTTP 阶段的转换层，没有第二套领域逻辑。

固定依赖：Rust `a2a-lf 0.4.2`、`a2a-client-lf 0.2.9`、`a2a-pb 0.3.2`、
`tonic 0.14`；Java A2A SDK `1.4.0.Final`、SOFARPC `5.13.0`。
本次 Native Mesh 为 `2.35.0`。公开构建不依赖 Native Mesh；真实 MOSN
验收需要操作者已有的应用身份、注册中心权限及环境配置。

## Harness 配置

在已有 A2A HTTP 配置、Provider 下行凭据与模型环境之上增加：

```sh
export DEMO_A2A_GRPC_ENABLED=true
export DEMO_A2A_GRPC_APP_NAME="$MESH_APP_NAME"
export DEMO_A2A_GRPC_HOST=127.0.0.1
export DEMO_A2A_GRPC_PORT=50001
export DEMO_A2A_GRPC_PUBLIC_URL="http://${MESH_TARGET_ADDRESS}"
```

`MESH_TARGET_ADDRESS` 是实际注册的目标应用地址与 ingress 端口，由部署提供。
每张 Bot Card 声明相同 gRPC 地址及不同 tenant。Bearer、A2A-Version、扩展
协商、tenant、RPC 方法和 deadline 均经 MOSN 透传；不从消息 metadata 选择
MOSN 目标。Triple 使用原生 protobuf 与服务端流，不经过 Layotto InvokeService。

REST 和 gRPC 共享 RequestHandler、H2 Task 存储、去重及事件管理。SOFARPC
Provider 使用 `tri`、`register=false`，服务发布交由部署流程。

## BCS 配置

在私有配置文件中添加，以下域名仅为占位示例：

```toml
[a2a_grpc]
endpoint = "http://127.0.0.1:50002"

[[a2a_grpc.routes]]
interface_url = "http://agent.example.org:51051"
target_app = "your-mesh-app"
service_name = "org.a2aproject.sdk.grpc.A2AServiceGrpc$AsyncService:1.0"
```

endpoint 只允许字面量 loopback HTTP origin。Card 中选定接口必须精确匹配
受信任 routes；没有路由时不能选择该 gRPC 接口。路由被冻结并写入关联指纹，
旧 Task 不会因为配置改动被发送到另一个应用。HTTP 的旧 context key 保持兼容。
BCS 只连接本地 MOSN，不对 Card 里的地址直连或做静默 HTTP 降级。

启动后沿用 Provider 管理接口配置每个 Bot 的 HTTP Card URL。先 GET 当前
revision，再 PUT `/providers/{provider_id}/bots/{bot_uuid}/a2a`，提交
`agent_card_url` 与 `expected_revision`；使用已有管理凭据，勿写入仓库或日志。
启用已有服务端队列，每个 Bot/Session 同时只有一个新消息 send。

## MOSN 部署检查

使用 Native Mesh 的原生 streamtriple listener 配置；egress/ingress 两侧均为
`grpc_proxy`，不能混用旧 HTTP/2 proxy。此版本包的模板与二进制存在路由名
差异，私有配置需核对 `sofa_egress_new_triple_router` 和
`sofa_ingress_new_triple_router`。HTTP 健康检查指向真实 SOFABoot 端口，
Triple 健康检查指向真实 50001。不要复制机器专属配置到公开默认值。

通过本机管理接口按顺序执行，检查 JSON `success=true`，不能只检查 HTTP 200：

1. 应用初始化：POST `/configs/application`，提交部署应用名、
   `parameters={}`、`multiProcess=false`、`remoteIp=127.0.0.1`、
   `remoteRpcPort=50001`。初始化会重置应用状态，只在启动流程执行。
2. 确认公开 Card 与原生 gRPC HTTP/2 listener 实际可用，再 POST
   `/configs/virtualapp/readiness`，设置 `readiness=true`。
3. POST `/services/publish`，服务名为上面的 `service_name`，
   `protocolType=tri`、`onlyPublishInCloud=false`；`providerMetaInfo`
   包含 `protocol=tri`、`version=4.0`、`serializeType=protobuf`、
   `appName` 及非空 `properties`（部署的 `app_name`、`zone`）。
4. POST `/services/subscribe`，提交相同 serviceName、protocolType，
   实际 `targetAppAddress`，以及 `vipEnforce=false`、`vipOnly=false`。
5. 核对注册诊断的精确服务条目有非空 dataList、发布端口正确、订阅 cluster
   已有可用 endpoint，并确认 ingress 完成 HTTP/2 握手。首次映射曾耗时
   78 秒；订阅返回 received=1 不能证明已经取得可调用地址。

本机管理端口为 13330，诊断端口为 11001。诊断查询为
`/api/v1/registry/publishers` 和 `/api/v1/config_dump?cluster={cluster_name}`；
按精确服务过滤，只输出计数和状态，不打印完整注册数据或请求头。
服务名包含 `$`，shell 中必须使用单引号或结构化 JSON，避免变量展开。

在本机观测到 50051 的非 loopback 连接被关闭，改用 51051 后真实链路通过。
这不证明代理是根因。保留模型进程的代理；只为 MOSN 移除代理环境变量。
重启 Harness 后曾出现 ingress 不监听而 health 仍 UP，需重验实际 listener
及发布状态；本次通过重启专用 MOSN 并重新初始化、发布、订阅恢复。
不要在有活动任务时执行应用重置。不要开启真实模型流量的完整 header trace，
MOSN 可能追加身份凭据。

## 验证与限制

Rust SDK smoke：`cargo run -p bcs-a2a-http --example harness_grpc`，环境提供
`AVERNET_A2A_CARD_URL`、`AVERNET_A2A_BEARER`、`AVERNET_A2A_MESH_CONFIG`
及 `AVERNET_A2A_MODE=stream|async`。它与完整 BCS 验收分开，不能相互替代。
Harness 正常 Maven 测试使用临时端口，不依赖公司 MOSN。

完整 BCS 验收复用 Harness `scripts/a2a_local.py` 的 text/tools/cancel 与
`verify_a2a_bcs_exec.py` 的 approve/deny 流程，凭据只读本地私有状态。
独立 `evidence/a2a-grpc-*.json` 记录实际构建哈希和结果。

SDK 对已终态 Task 的直接订阅可能返回 UNIMPLEMENTED，BCS 通过有界 GetTask
读取权威终态；不伪造 SDK 重放能力。ask_user、多实例接管及进程重启后在途
事件无损恢复仍不在本次范围。Native Mesh 重启编排尚需部署方纳入运维流程。
