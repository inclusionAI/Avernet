# Plan: TC Session Resource Withdrawal

## Authorization
2026-09-28 用户授权“直到可以提交 PR”，据此连续推进 plan、tasks、实现及验证，不包含 commit、push 或创建 PR。接口尚未冻结属于上线前置条件，本次提交 TC 侧默认暂停投递的实现。

## Approach
统一在 SessionResourceService.delete 传入允许通知的单聊 scope 集合（personal_bot_chat、friend_bot_chat、openapi_session）。Repository 在真正事务中完成状态 CAS 和 outbox 插入；只有首次状态转换生成稳定事件。旧已删除数据不自动回填，重复删除不重置通知状态。群聊和未知 scope 保留原删除行为而不生成通知。

独立 worker 通过仓储协议领取持久事件。每次最多领取一个、处理完成再领取，数据库时间和随机 lease token 防止多实例重复领取及过期 worker 覆写；崩溃后 lease 到期可恢复。至少一次投递靠 ECB 对稳定 event_id 幂等。默认暂停 worker，但始终保存删除事实。串行 worker 控制线程和请求数量，不增加不必要的并发配置。

每个 worker 只处理配置指定 tenant；凭据须与该 ECB 集成租户一致，不支持隐式跨租户 fallback。

## Boundaries and Files
以下路径相对仓库根目录，基线落点详见 research.md。
- core/session_resources/service.py:371：传入单聊 scope 策略，不做 HTTP。
- core/repository/protocols/platform.py:489、implementations/platform/session_resource.py:194：soft_delete 增加明确允许的 scope 参数，transactional_orm_session 保证更新与插入原子性；仓储不自定场景策略。
- core/session_resources/withdrawal_{types,models,worker}.py：状态快照、ORM、调度和重试策略。
- core/repository/implementations/platform/resource_withdrawal.py：领取、fenced 完成、查询统计和 blocked-only 重放。
- plugin_api/tc_resource_withdrawal.py、plugins/{community,local}/tc_resource_withdrawal.py：版本化插件契约、HTTP 回执校验、显式 test fake；生产与 singlebox 均不自动使用 fake。
- di/modules/tc_resource_withdrawal_module.py、di/tc_resource_withdrawal_config.py、di/container.py：严格配置、SecretResolver 和生命周期。独立 secret name 不复用旧链路的 local dummy token。启动显式解析 worker，避免生命周期发现吞掉配置异常。
- core/schema.py、core/session_resources/sql/ac_tc_resource_withdrawal.sql：增量建表，无历史回填。
- docs/contracts/ 与本 feature 下 runbook.md：草案协议和迁移、暂停、查询、受控重放、回滚与上线门槛。

## Contract
草案 POST /api/v1/knowledge/integrations/tc/files/withdraw-by-resource，配置 HTTPS origin 和 SecretResolver 服务凭证；body 仅 event_id、res_id。event_id=tc.resource.withdrawn:{res_id}。2xx 必须包含精确匹配 event_id、accepted=true（不能是 truthy）、status=applied|pending，否则 blocked。accepted 仅代表 ECB 持久接收，不是业务失效完成。插件版本 v1 不擅自增加草案不存在的 wire 字段。

HTTP timeout/网络故障/408/429/5xx 指数退避至上限，达到次数上限转 blocked 并告警；401/403/其他4xx/无效回执立即 blocked。未知程序错误也 blocked，不泄漏响应正文或凭据。不跟随重定向（已用真实 HttpxClient + 本地 307 HTTP 服务验证）。DB 失败保留租约供恢复，不假记成功。重放只允许指定 blocked event_id + 预期 attempts，附操作者与原因审计；accepted 永不可重放。

## Validation
先写失败测试，再实现：真实 SQLite 文件库（独立连接）事务回滚、并发删除、重启、领取互斥与 fencing；所有资源状态、鉴权失败、单聊/未知/群聊隔离；worker 成功、退避、阻断、恢复、暂停；HTTP 精确回执及错误分类；DI 默认与错误配置；world fixture 插件消费者契约；现有新旧删除入口和 materialized callback 回归。
运行最邻近测试、Backend 架构/契约门禁、Backend 全量可运行测试和静态门禁；记录无法运行的环境依赖。检查每个源码文件 <=1000 行、无无关文件变动及敏感信息。真实 ECB 乱序/引用隔离不以 fake 测试冒充，列入部署前检查。

## Risks and Rollout
先建表再部署 TC（新删除始终写表），确认 ECB 契约/集成租户隔离后配置 endpoint/secret 并启用 worker。暂停保留所有状态。回滚旧代码将停止新事件记录，需先暂停删除入口或回滚期间人工补偿，不清表。生产 MySQL 的真实事务和 DDL 需部署环境演练；本地 SQLite 的结果不等价于生产实测。告警通过结构化日志及只读统计输出接入现有日志平台，SLA/阈值/负责人仍待双方确定。
