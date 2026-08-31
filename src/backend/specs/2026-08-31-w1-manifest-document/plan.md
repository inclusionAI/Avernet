# W1 plan — 拆解与顺序

> 源:`docs/superpowers/plans/2026-08-31-bot-config-manifest-implementation-plan.md` Phase A。
> 单人串行;每步测试先行、红了再绿;PR 单提交 squash。

1. **存储层**——DDL、ORM(长度前缀 manifest_key、租户守卫)、仓储协议/实现(整份替换+并发重试)、仓储测试(内存 SQLite)。
2. **schema 文档模型**——六类目 pydantic 模型(extra=forbid 落实 apply_once/engine_ext 不可写)、`parse_document`/`validate_document` 规则码表、校验矩阵测试。
3. **能力与开关**——`capabilities.supported_categories(engine_type, bot_type)` 单函数两入口(fail closed)+`feature_flags`(BCM_API_ENABLED,默认关)。
4. **服务层**——协议(自有 core 模块)+实现(all-or-nothing PUT、未引用源告警、identity type 引擎校验、#935 窄覆写)+api re-export+DI 绑定+_PAIRS。
5. **公开面**——bots/router 4 路由(暗启动)、ADMISSION/AUTHORIZATION 各 4 行、错误映射(422+逐条 violations on data、404 dark)、`bots.openapi.json` 手工增量(append-only)。
6. **验证**——适配层用例+endpoint-framework 用例(@endpoint_test,含 JWT principal)+共享 router 回归+`ci_test.sh` 变行覆盖。

## 与既有的协作面(本次实现时踩到的)

- 与 d8a909ce(engine vocabulary)同分支并行:两改动只在 bots/router.py、schemas.py、gateway json 相交;最终态=两者语义都在(上游 c2a88e→d8a909ce 之上逐层叠加)。旧 IDE buffer 覆盖曾两次把已落内容回退——**同树并行开发需留意**。
- GRANT_CHECKED(USER_GATED)路由**零** gateway 侧配置改动(REFUSED 才需 avernet/ocb 双写)——本 PR 只有该系路由。
