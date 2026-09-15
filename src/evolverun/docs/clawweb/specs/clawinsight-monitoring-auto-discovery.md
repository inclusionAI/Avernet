# ClawInsight 无配置监控发现

日期：2026-09-15。替代静态 Bot 名单和启用开关，不修改既有治理或鉴权逻辑，不包含独立的管理员权限改造。

## Problem / Decision

采集端扩容不应要求 ClawWeb 同步部署第二份名单。删除 OCB 启动脚本中的
`CLAWWEB_MONITORING_ENABLED`、`CLAWWEB_MONITORING_BOTS_JSON`，Avernet 完全忽略这两个旧变量。
保留既有 Host 装配入口与数据库生命周期；运行时构造不访问数据库，存储故障只在监控请求中返回未就绪。
仅保留已有的 stale-seconds 配置，不扩大本次改动到其他配置或 Host 架构重构。

## Service API / Plugin API

- HTTP 路径、请求及成功 ACK 的 JSON 结构保持不变，schemaVersion 不变。
- `MonitoringApi.bots()` 改为异步；HTTP adapter 必须 await 并沿用错误映射。
- `MonitoringStore.listBots()` 返回两个既有表的 bot_id 去重并集，二进制排序。
- 数据库失败必须返回 503 / MONITORING_NOT_READY，不得返回空列表或成功 ACK。
- 合法新 Bot 可直接写入；不再产生 MONITORING_BOT_NOT_ALLOWED。保留 engine 的 OC/TE 枚举、ID/字段/时间/大小校验。
- 未上报 Bot 的读取返回 404 / MONITORING_BOT_NOT_FOUND；已有检查但没有诊断的 Bot 返回空诊断页。
- 仅诊断上报也能发现 Bot，状态 UNKNOWN；最新检查为 PAUSED 时保持 PAUSED，直到后续有效检查替换。
- botId 仍为全局身份：不同 Bot 必须有不同 ID，同名 default 不能作为两个实例的共同 botId。
  本次不引入引擎绑定注册表；engine 为每次上报的校验字段，BotCheck 仍按 botId 和检查时间决定最新状态。

## Persistence / Security

复用 insight_monitoring_bot_checks 和 insight_monitoring_diagnoses，不新增表、不清理历史、不回灌或修改采集进度。
成功写入即可被当前实例、其他连接和重启后的实例发现；失败上报不能创造名单项。
旧表中已有数据自动可见，列表不是“当前计划采集”的配置清单，也不会因 stale 自动移除 Bot。

移除名单限制意味着能访问内部 POST 的调用方可以写入任意格式合法的 botId；
部署方必须继续限制 `/api/insight/v1/internal/monitoring/*` 为受信采集端可达的内部入口，不能直接公开。
保留现有网络信任边界、Host 的既有 GET 访问控制、请求体限制、幂等与写入失败处理，不新增 Token 或绕过已有鉴权。

## Consumers / Compatibility

- CV：无需生成服务端名单，不再先等待 GET 返回全部目标 Bot 才开始采集；先真实上报，再比对目标 ID 集合。
- Web：初始空列表是正常状态；每 30 秒、手动刷新及页面恢复可见时重新发现 Bot；保留当前选择和诊断筛选。
- OCB：删除两个默认导出，不新增替代变量；发布必须使用包含本改动的 Avernet 构建。
- 旧环境变量残留不影响新版本，但不会作为回退读取；旧版本回滚仍可能依赖名单，应按旧版本部署要求恢复配置。

## Validation / Rollout

1. 无旧变量、残留 false/非法 JSON、残留旧名单三种状态下均可启动监控。
2. 同一运行实例接收 26 个 Bot（17 OC / 9 TE）的检查和诊断，列表无截断且去重。
3. 重启与不同数据库连接读到同一持久化名单，历史诊断-only 和检查-only Bot 均可见。
4. 非法请求和写入失败不新增名单项；读失败返回 503；未知 Bot 读取 404。
5. 新增 Bot、空列表转非空无需刷新整个页面；隐藏页面和卸载停止轮询。
6. 既有访问边界、乱序检查、诊断幂等、持久化与原有治理回归通过。

上线先更新 Avernet 构建和 OCB 启动脚本，然后由 CV 发真实检查/诊断并核对每个目标 ID、状态和记录。
不要求名单长度恰好为 26（历史 Bot 可能更多）；要求 26 个目标全部存在且不重复。
需要止损时暂停 CV 发送或限制既有内部入口，保留 outbox 和监控表；不再用已删除开关停用服务。
