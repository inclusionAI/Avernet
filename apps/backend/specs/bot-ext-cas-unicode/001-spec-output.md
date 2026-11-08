---
agent: tc-review
status: completed
created: 2026-10-20
iteration: 2
---

# Bot ext CAS 中文序列化修复

## 需求与授权

基于 GitHub inclusionAI/Avernet 的远端 dev 修复 Bot.ext 的 TEXT CAS 中文序列化不一致。初始基线为 `b79b220fc1af34244430b2ce06652e6195adad93`，开发分支为 `fix/bot-ext-cas-unicode-20261020`。交付到以 dev 为底的 PR，自动处理本次变更引起的测试/CI 错误。不部署、不发生产回调、不修改线上数据。

用户已明确选择最小 `ensure_ascii=False` 方案并授权创建 PR；本 spec 可直接作为编码与评审输入，无需再设置人工审批步骤。本文件是方案产物，尚不代表测试或交付已完成。

已读取根 AGENTS.md、CONTEXT-MAP.md、架构约束，以及 TeamClaw OPTIMIZE.md 中 2026-10-19 的 TEXT CAS 经验。该经验指出中文转义、空格和键顺序都可能造成重构快照不匹配；本次只修中文转义这一项。

## 编码 Spec

### 既有链路与根因

1. `core/repository/implementations/devices/device.py:update_bot_start_status` 在设备启动状态链路中读取 Bot.ext，合并 start_status/start_message，以 `json.dumps(ext, ensure_ascii=False)` 写回 TEXT。
2. BotModel 返回解析后的 ext 对象。`BotRepository.compare_and_set_ext` 再以默认 `json.dumps(expected_ext)` 重构期望 TEXT，并在一个 UPDATE 中按 ext 原值匹配。
3. 默认 ensure_ascii=True 将中文重构为 Unicode 转义，原 TEXT 若存储未转义中文，就算对象内容一致也会影响零行。
4. 调用方把 None 解释为 CAS 未更新，例如 `arca_image_pin.persist_default_arca_image_policy` 读新快照并最多重试三次，最终抛出 `Bot image policy CAS conflicted repeatedly`；`RestartState.update/ensure` 同样依赖该原语并有各自重试预算。此次不修改调用方预算或状态机。
5. 现有 `test_compare_and_set_ext_preserves_concurrent_bot_metadata` 通过 repo.insert 写入中文，insert 也使用默认转义，因此两边恰好一致，未覆盖第 1 步的真实持久化格式。

这是源码支持的触发机制；本 PR 的本地测试不能替代生产部署版本、实际数据和事故因果验证。

### 最小修复范围（兼容证据调整）

真实SQL测试已证明：仅把CAS的expected编码改为ensure_ascii=False，会让原先可匹配的历史ASCII转义中文TEXT返回None；现有Bot.insert/update也使用默认转义，不能通过同步修改writer或把历史数据失败写成验收来掩盖回归。主编排据此调整方案，保持修复仅限CAS方法。

- 保持`_new_bot_model`、`update_by_owner`、share_policy及其他writer原样。
- CAS期望对象生成两种编码：`json.dumps(expected_ext, ensure_ascii=False)`和旧`json.dumps(expected_ext)`；使用SQLAlchemy绑定参数的单条`IN`谓词匹配任一种。
- CAS替换ext用`ensure_ascii=False`，成功后中文原文统一不转义。
- expected_ext=None继续只使用SQL IS NULL。
- 原owner/env/is_delete筛选不变；内容不同的真实冲突仍返回None，不吞数据库异常。
- 不更改separators、indent或sort_keys；不引入公共serializer、raw snapshot重构或二次UPDATE。

### 历史格式兼容边界

两种Python既有编码的中文TEXT、纯ASCII及NULL均保持支持。键顺序、空白和自定义格式差异不在本修复范围；不声称JSON语义等值或所有MySQL/OceanBase排序规则已验证。Draft CAS已通过ConfigArtifactOffloader.prepare使用ensure_ascii=False，单独用真实SQL测试确认，无需改Draft实现。

### 关键方法与领域模型

无新增方法抽象或领域模型；沿用 BotRepository 作为持久化边界。现有模型已足够表达需求，变化只有 TEXT 编码策略。

| 既有方法 | 职责与输入输出 | 副作用与不变量 |
| --- | --- | --- |
| `_new_bot_model` | Bot 数据构造 ORM 模型 | 本次保持不变 |
| `update_by_owner` | 接收既有允许字段，返回更新 Bot 或 None | 本次保持不变 |
| `compare_and_set_ext` | bot_id、owner_id、expected_ext、新 ext；返回 Bot 或 None | 单条条件 UPDATE；真冲突不写入；数据库异常继续传播 |

| 既有字段 | 类型及来源 | 约束与兼容影响 |
| --- | --- | --- |
| bot_id / owner_id | str，调用方身份 | 必填；匹配范围不变 |
| env / is_delete | 现有环境上下文与 ORM 字段 | env/live row 隔离不变 |
| expected_ext | dict[str, Any] 或 None，读取快照 | None 匹配 SQL NULL；dict按未转义/旧转义两种JSON编码匹配 |
| ext | dict[str, Any]，调用方合并后的对象 | CAS 新值必填；只改变非 ASCII 编码表示，不删除或重建业务键 |
| gmt_modified | 数据库时间 | 仅现有 UPDATE 路径写入，不新增状态字段 |

### 诊断日志

在 CAS 结果位置增加或复用一条有明确事件名的诊断日志，包含 operation=compare_and_set_ext、bot_id、affected_rows、expected_ext_is_null、serialization=unicode；零行只标记 unmatched，不能断言一定发生真实并发。成功可用 debug，未匹配可用 info，沿用现有 logger 风格。

不得输出完整 ext、expected_ext、SQL 参数或异常中可能携带的原始 JSON；不得输出 ext 的键值、start_message、凭据或 token。无需新增内容 hash 或额外查库。日志测试应验证成功与未匹配事件，并使用包含秘密哨兵的 ext 断言其不出现。

本次没有新增 backend/engine/relay HTTP 边界，不新增接口入参/响应日志。数据库仍通过既有 DatabasePlugin/ORM 操作。

### 文件范围

| 文件 | 允许变化 |
| --- | --- |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/bot.py` | 仅CAS方法内双期望编码、Unicode新值与最小结果日志 |
| `apps/backend/tests/community/repository/bot/test_bot_ext_cas_unicode.py` | 独立真实TEXT回归、双编码/None/ASCII/冲突与日志，复用既有fixture；不扩充已超过1000行的旧测试文件 |
| `apps/backend/specs/bot-ext-cas-unicode/001-spec-output.md` | 本方案与验收说明 |

禁止修改回调路由、启动身份校验、Binding 状态机、镜像策略、重启流程、发布 ext offload、数据库 schema、依赖锁、前端、其他 JSON 字段。只有本次验证暴露且确属本次补丁引入的问题才自动修复；无关失败记录清楚，不为通过测试扩大改动。

## Review Spec

- [ ] changed diff 每一行对应本需求，无无关 writer 统一或重构。
- [ ] 原有 owner/env/is_delete 和 ext 快照谓词完整保留，None 分支不变。
- [ ] 未转义中文原始 TEXT 用真实 ORM seed 复现，基线应失败、修复后成功，不能只 mock SQL 或只断言 dumps 参数。
- [ ] 新 ext 持久化文本也含真实中文，中文键与值均不转义；解码后业务内容不变。
- [ ] 现有insert/普通update与其他JSON字段不变，产生的旧格式仍可CAS。
- [ ] 真并发快照变化返回 None，保留并发写入的全部内容。
- [ ] 历史转义中文TEXT成功CAS并拒绝后续stale快照；不扩展到任意JSON格式等值。
- [ ] 不吞数据库错误、不增加重试、不绕过 CAS。
- [ ] 诊断字段有用且不泄露 ext/秘密，未新增未使用 import/变量、冒号前空格或不以 '# ' 开始的 block comment。
- [ ] 复用既有源码文件规模门禁；不得为这个局部修复拆大文件或放宽全局 CI。

## QA Spec

本地、隔离SQLite数据库，新增独立测试模块并复用现有 `_FileSqliteDB`、repo fixture 与 BotModel；无需生产数据或回调。

| 编号 | 场景与步骤 | 预期 |
| --- | --- | --- |
| U01 | repo.insert 后通过 ORM 直接写 `json.dumps(snapshot, ensure_ascii=False)` 的中文键/值 TEXT；读取对象再 CAS | 成功，新增字段保留，完整新 ext 符合预期；原始落库文本不含中文对应的转义串 |
| U02 | 在 U01 的成功结果上携带旧快照再次 CAS | 返回 None，新值及并发字段不被覆盖 |
| U03 | 纯 ASCII snapshot，正常 CAS 后再旧快照 CAS | 首次成功、旧值拒绝，原有格式不变 |
| U04 | SQL NULL ext，expected_ext=None CAS 写入含中文新值 | 成功，NULL 语义不变，新值未转义 |
| U05 | 已有非 NULL ext，expected_ext=None | 拒绝更新，原行不变 |
| U06 | 直接 ORM seed 默认 ensure_ascii=True 的历史中文 TEXT | CAS成功、返回新ext且新TEXT未转义；随后旧快照拒绝 |
| U07 | insert 与 update_by_owner 分别写中文 ext | writer继续原有默认转义，后续CAS成功；非ext JSON策略维持原样 |
| U08 | owner/env/live row 不匹配 | 复用现有隔离测试，零串写 |
| U09 | 成功及未匹配 CAS 携带秘密哨兵 | 诊断包含事件与行数，日志不含哨兵及完整 JSON |

真冲突使用两个不同快照/第二写者的数据变化复现，不依赖随机并发时序。读取 API 与原始 TEXT 两种断言并用，避免“解析后相等”掩盖编码问题。

## PR 验证与 Ship Spec

1. 在 backend 的 Python 3.12 公共依赖环境中，先运行新增 U01 证明基线失败，再实现最小补丁并重跑；记录实际命令及结果。
2. 从 `apps/backend` 执行 `uv run pytest tests/community/repository/bot/test_bot_repository_unified.py -q`，确认完整相邻 repository 用例通过。项目 fixture/conftest 有要求时沿用正式 runner，不为局部测试改全局配置。
3. 运行消费者相关回归：`tests/community/core/service_bot/services/test_arca_image_pin.py` 及 `tests/community/core/bot_management/aicoding/test_restart_persistence.py`；若修改了其他消费者相关逻辑，补跑对应现有测试。
4. 补跑Draft中文CAS测试，确认既有Unicode编码不需实现修改。运行仓库既有 lint/SAST、必要的 backend module gate，检查 changed-line coverage 与新增分支覆盖；以实际报告记录覆盖率和缺口，不能把仅相关用例的整文件低覆盖误报成逻辑失败或通过。遵循 PR/CI 已配置的真实阈值，不降低阈值或添加豁免。
5. 安装该 worktree 的仓库 hooks，推送前以最新远端 dev rebase，保留 dev 的并行变更；冲突只处理本修复范围。rebase 后重新执行受影响验证。
6. 创建目标 dev 的 PR，标题建议 `fix(backend): preserve unicode in bot ext CAS`。按仓库 Problem/Solution/Validation/Compatibility and risk 结构说明触发条件、真实验证与历史 TEXT 边界。
7. 跟进 PR CI/测试错误并自动修复本补丁引起的问题，重跑相应检查。未能运行的门禁与环境限制如实记录，不伪造通过；不自动合并。
8. 本次 Ship 到 PR 为止：不部署、不生产回调、不直接更新数据库、不更改其他工作树或他人代码。按 TeamClaw 要求由主编排统一追加 log.md；有可复用新经验时追加 OPTIMIZE.md。

回退：通过后续 Git revert 撤销代码补丁；本任务没有数据迁移。由于撤销不能自动还原已经写入的 TEXT 格式，回退后同样存在混合格式边界，不能宣称数据库格式自动恢复。
