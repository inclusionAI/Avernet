# Target Environment Passport Repair Implementation Plan

> **执行方式：** 使用 `implement` 技能逐项落地；每项先写失败测试，再写最小实现，最后运行受影响回归。

**Goal:** 允许部署在预发的 OCB 运维接口显式传入 `target_env=prod`，并让 tcauthmng 在共享数据库中按生产环境命名空间完成 default bot 的 Passport 查询、首次申请、回查和 AgentHub 注册，同时保持未传环境的正常创建链路行为不变。

**Architecture:** `DefaultBotPassportRepairService` 是唯一发起显式目标环境的业务入口。Avernet 的 `PassportPlugin` 将 `target_env` 作为可选关键字参数加入协议；OCB prod adapter 把它序列化为 tcauthmng DTO 的 `env`；tcauthmng 在 Facade 边界只解析一次目标环境，并把同一个值传给 DB agentId、记录 env 字段和 AgentHub AppEnv。ACM 不传环境也不修改，因为预发与生产共用同一套 ACM 数据。

**Tech Stack:** Python 3.12、pytest、Ruff、Java 8、JUnit 5、Mockito、Maven、Hessian/Layotto DTO。

---

## Task 1：锁定 Avernet 修复服务的目标环境契约

**Files:**

- Modify: `src/backend/tests/community/core/bot_management/services/test_default_bot_passport_repair_service.py`
- Modify: `src/backend/tests/community/contracts/test_passport.py`
- Modify: `src/backend/tests/community/plugins/community/test_passport.py`
- Modify: `src/backend/src/agentclaw/community/plugin_api/passport.py`
- Modify: `src/backend/src/agentclaw/community/plugins/local/passport.py`
- Modify: `src/backend/src/agentclaw/community/plugins/community/passport.py`
- Modify: `src/backend/src/agentclaw/community/core/bot_management/services/default_bot_passport_repair_service.py`

1. 在修复服务测试中断言初始查询、`apply_first_agent_passport` 和申请后回查都收到 `target_env="prod"`。
2. 在协议/实现测试中覆盖：显式 `pre`/`prod` 可用；未传参数保持现有行为；非法显式环境在调用边界失败。
3. 运行 RED：

   ```bash
   cd src/backend
   uv run pytest -q tests/community/core/bot_management/services/test_default_bot_passport_repair_service.py tests/community/contracts/test_passport.py tests/community/plugins/community/test_passport.py
   ```

4. 为四个运维所需方法增加关键字参数 `target_env: str | None = None`：`apply_first_agent_passport`、`query_auth_status`、`query_token`、`query_agent_passport`。
5. 修复服务所有 Passport 调用显式传 `target_env`；local/community 实现验证显式值但不改变自签发语义。
6. 重跑同一命令至 GREEN，并运行 Ruff。

## Task 2：让 OCB prod adapter 把目标环境写入 Hessian DTO

**Files:**

- Modify: `src/backend/tests/corp/plugins/test_passport_admins.py`
- Modify: `src/backend/src/agentclaw/corp/plugins/prod/passport.py`

1. 增加 DTO 测试：`ApplyAgentPassportRequestDTO` 和 `BotRequestDTO` 显式 `prod` 时序列化 `env`，参数为 `None` 时不出现 `env`。
2. 增加 adapter 测试：Apply First 与三种查询方法均把 `target_env` 传入对应 DTO。
3. 运行 RED：

   ```bash
   cd src/backend
   uv run pytest -q tests/corp/plugins/test_passport_admins.py
   ```

4. 给两个 DTO 增加可选 `env` 字段，并仅在非 `None` 时序列化。
5. 对齐 `PassportPlugin` 新签名；只在 Apply First 与三种查询调用中使用 `target_env`，正常 `apply_agent_passport` 保持旧行为。
6. 重跑同一命令至 GREEN，并运行 Ruff。

## Task 3：让 tcauthmng Facade 按目标环境访问共享 DB

**Files:**

- Modify: `facade/common-facade/src/main/java/com/alipay/tcauthmng/common/facade/model/passport/ApplyAgentPassportRequestDTO.java`
- Modify: `biz/biz-service/src/test/java/com/alipay/tcauthmng/biz/service/facadeimpl/AgentPassportFacadeImplApplyAgentPassportTest.java`
- Modify: `biz/biz-service/src/test/java/com/alipay/tcauthmng/biz/service/facadeimpl/AgentPassportFacadeImplQueryTokenTest.java`
- Modify: `biz/biz-service/src/test/java/com/alipay/tcauthmng/biz/service/facadeimpl/AgentPassportFacadeImplQueryAuthStatusTest.java`
- Modify: `biz/biz-service/src/test/java/com/alipay/tcauthmng/biz/service/facadeimpl/AgentPassportFacadeImplQueryAgentPassportTest.java`
- Modify: `biz/biz-service/src/main/java/com/alipay/tcauthmng/biz/service/facadeimpl/AgentPassportFacadeImpl.java`

1. 在 Apply DTO 增加 `env` 字段。
2. 新增跨环境测试：运行环境为 PREPUB、请求 `env=prod` 时，Apply First 和三种查询均使用 `prod|owner|bot`；新增非法显式环境提前失败的测试。
3. 对新写入 token/user passport/agent passport/registry 记录，捕获模型并断言 `env=prod`、`agentId=prod|owner|bot`。
4. 运行 RED：

   ```bash
   mvn -pl biz/biz-service -am \
     -Dtest=AgentPassportFacadeImplApplyAgentPassportTest,AgentPassportFacadeImplQueryTokenTest,AgentPassportFacadeImplQueryAuthStatusTest,AgentPassportFacadeImplQueryAgentPassportTest \
     -Dsurefire.failIfNoSpecifiedTests=false test
   ```

5. 新增目标环境解析函数：缺省回退当前运行环境；显式值只接受 `pre`、`prod`，并在任何 DB/ACM/AgentHub 副作用前验证。
6. Apply First、Apply、Query Auth Status、Query Token、Query Agent Passport 在入口解析一次，并把结果贯穿所有递归 helper；禁止 helper 再次读取当前环境。
7. 所有 DB agentId 与新记录 `env` 使用解析后的目标环境；ACM 调用保持原样。
8. 重跑同一命令至 GREEN。

## Task 4：让 AgentHub 注册使用目标 AppEnv

**Files:**

- Modify: `biz/biz-service/src/main/java/com/alipay/tcauthmng/biz/service/HubService.java`
- Modify: `biz/biz-service/src/main/java/com/alipay/tcauthmng/biz/service/impl/HubServiceImpl.java`
- Test: `biz/biz-service/src/test/java/com/alipay/tcauthmng/biz/service/impl/HubServiceImplTest.java`
- Modify: `biz/biz-service/src/main/java/com/alipay/tcauthmng/biz/service/facadeimpl/AgentPassportFacadeImpl.java`

1. 新增测试捕获 `AgentHubFacade.register` 请求，断言预发进程显式 `prod` 时 `AgentApp.appEnv=PROD`，未传时仍按当前环境解析。
2. 运行 RED：

   ```bash
   mvn -pl biz/biz-service -am -Dtest=HubServiceImplTest -Dsurefire.failIfNoSpecifiedTests=false test
   ```

3. 为 `registerAgentV2` 增加带 `targetEnv` 的重载，旧签名委托新签名以保持兼容。
4. `AgentPassportFacadeImpl#getOrRegisterAgentCode` 把同一个已解析环境传给 HubService；`pre` 映射 `AppEnvEnum.PRE`，`prod` 映射 `AppEnvEnum.PROD`。
5. 重跑同一命令至 GREEN。

## Task 5：跨仓库回归、代码审查与提交

1. Avernet：运行上述三组测试、相关 bot management tests、Ruff 和类型检查（若仓库现有命令可用）。
2. OCB：运行 corp Passport adapter tests、关系插件 tests 和 Ruff。
3. tcauthmng：运行四个 Facade 测试、HubService 测试，再运行 `biz-service` 模块测试。
4. 手工审查完整 diff：确认正常创建链路未传 `target_env` 时语义不变；确认 ACM 无改动；确认无 URL、token、环境硬编码泄漏。
5. 分仓库提交。tcauthmng 提交时排除工作区原有的无关空白改动。

## 部署与验收顺序

1. 先在预发部署 tcauthmng 新版本，再部署包含 Avernet 新接口和 OCB prod adapter 的 OCB 预发镜像。
2. 用单个生产 default bot 调运维接口，传 `target_env=prod`。
3. 验证接口结果：`passport.status=ISSUED`、`token_present=true`、`ext_agent_code_verified=true`、owner relationship verified。
4. 验证共享 DB：相关表记录的 `agent_id` 前缀和 `env` 均为 `prod`；不存在同 bot 的新增 `pre|...` 记录。
5. 验证 AgentHub 新注册数据为 PROD；ACM 授权关系可查询。
6. 对同一 bot 重复调用，结果应为已验证/幂等，不新增重复记录。
7. 批量处理目标用户后，在线上环境重启对应 bot，使 agentCode 写入 Arca 容器 credential 文件。
