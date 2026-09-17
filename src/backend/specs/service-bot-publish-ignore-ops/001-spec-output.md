# 服务 Bot publish-ignore：当前阶段运维接口

源码及PR：GitHub inclusionAI/Avernet，目标REL20260917，PR #2254。仅实现和验证，不部署或合并。

## 最新范围

取消version参数，不支持指定历史发布版本。支持draft、verify、online当前实例；保留cp，不引入tar，不修改Docker启动脚本，不触发发布、重启或业务文件删除。

Backend：POST `/api/service-bot/publish/ops/publish-ignore`

```json
{"bot_id":"service_bot_id","entity_id":"entity_id","stage":"draft","operation":"add","path":"workspace/project/node_modules"}
```

- 请求模型不接受version等额外字段，避免调用方误以为指定版本生效。
- operation=add|remove；path为单个精确相对路径，允许规范化./前缀和尾斜杠；拒绝绝对路径、路径穿越、控制字符、glob、反斜杠、重复斜杠及开头#或!。
- 管理员可操作所有Bot；其他用户复用现有Bot管理权限（ADMIN/OWNER）。entity_id只负责准确查询，不替代授权。

## Backend定址与连接

1. 按bot_id/entity_id查询Bot并执行管理权限检查，要求service类型。
2. 复用 `RuntimeBindingResolutionService.resolve(RuntimeBindingRequest(...))`。owner_id取已授权Bot记录，actor_user_id取真实操作者，stage取请求；显式target=CALLER_SERVICE，防止Caller Bot的AUTO分支选到个人调用实例。
3. draft使用Bot主记录binding_id，不依赖发布单ext.binding.draft；verify/online完全沿用共享解析器的当前阶段选择规则。共享解析器保持原样，不增加version或device_id回退。
4. 复用解析器的绑定有效性检查，仅加载设备元数据分派baas/arca。没有可用阶段时返回stage_not_bound；不额外扫描发布版本、阻断发布状态或做多轮事后快照检查。
5. BaaS枚举本次副本并固定device_uuid；ARCA对应选中绑定的一个目标。逐目标复用 `DeviceContextResolver.resolve_for_binding_invoke` 取得connection；BaaS通过既有DeviceAdapterTransport调用，ARCA通过注入HTTP客户端和解析出的URL/headers调用。不使用用户提供的URL，不更改通用连接组件。

API/router保持薄适配，领域服务不引入HTTP框架。命令/绑定值类型位于kernel，运行调用协议位于plugin_api；DI复用既有RuntimeBindingResolutionService及DeviceContextResolver。

## Engine校验与文件变更

Engine：POST `/api/bot/publish-ignore`。expected_target仅包含bot_id/entity_id/stage，另有operation/path/request_id和Backend签名。不再传递或校验version，也不再为此扩展Credentials.version。

- Backend独占Ed25519私钥 `SERVICE_BOT_PUBLISH_IGNORE_SIGNING_KEY`；Engine只持公钥 `SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY`。签名在完成Bot管理授权后生成，不以普通聊天token代替管理授权。
- Engine加载真实凭证核对bot/entity/stage；缺失或错配拒绝。每次请求有独立request_id和签名时间窗；固定锁内持久化已消费request_id，阻止重放。
- 固定操作 `/home/admin/.service_bot_publish_ignore`，不接受自定义目标文件。使用有界flock、nofollow、普通文件/大小检查、同目录临时文件及原子replace；保留其他规则、注释与空行。add已存在或remove不存在为幂等成功；失败不宣称changed。
- 保留结构化请求/响应/失败日志：stage、Bot/entity、真实operator、request_id、binding_id/target_id、耗时和错误类别；不记录签名密钥、token、认证头或整份凭据。

## 结果与边界

响应包含success、scope=current_instances、逐目标results和request_id；各目标status为changed、unchanged、failed或unknown（超时）。只有选定目标全部成功才success=true，部分失败保留成功结果。

不提供targets_changed/snapshot_status，不保证并发发布或扩缩容的一致性；成功仅代表本次选中的实例。规则位于实例home，不按版本隔离，不自动复制到其他阶段、新实例或替换的home。

ignore消费者位于独立agentclaw-daas-scripts仓库，是后续安装生效的前提。此PR不修改cp/chown算法。生产NAS锁/rename及真实部署验证不由本地临时目录测试替代。

## 验证计划

- 无version请求可调用，携带version被明确拒绝；Backend签名→Engine真实HTTP/DI/文件测试覆盖三阶段、BaaS/ARCA、增删幂等、身份错配和重放。
- 真实DB：draft仅有Bot主绑定、无发布单仍成功；verify/online的发布绑定与Bot主绑定不同，必须选中正确当前阶段。
- 真实共享解析器：Caller Bot显式选择服务阶段而非Caller实例；协作者与管理员使用Bot真实owner查找、保留actor审计；未授权/阶段不存在/无有效绑定拒绝。
- 运行Backend/Engine定向与全量CI，变更行覆盖≥90%；检查架构依赖、未使用import/变量、安全扫描及git diff --check。
- Docker启动脚本相对release merge base无差异。更新PR说明与本地证据，pending远端检查不记作通过。
