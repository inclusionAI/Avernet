---
agent: tc-code-reviewer
status: completed
created: 2026-09-05T20:40:00+08:00
iteration: 8
---

# 代码评审报告

## 评审范围

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/claude-code-session-source-filter`
- 分支: `feat/claude-code-session-source-filter`
- 基线: `origin/dev` / `380a992040afb93dd9d3f06f4b5c50ba401fb9d7`
- 当前提交状态: `HEAD` 仍为基线，最新修复在未提交工作区；正式 ACI head 尚不可用。
- 本轮重点: adapter conversion failure event、create 下游 builtin `PermissionError` 精确测试，以及权限异常安全响应、用户过滤规则、AuthContext seam、兼容性和全量回归。
- 操作边界: 仅审查和验证；未修改业务代码，未 commit、push、rebase、创建 PR。

## 最终核对结论

| 检查项 | 结论 | 说明 |
|---|---|---|
| Adapter conversion failure event | PASS | `session.py:332-343` 输出 `event=claude_code.sessions.list.failure`、operation、status=500、reason=conversion_error、duration_ms，仅记录 raw_type/key_hash/key_format/error_type，不输出 raw session、原始 key 或异常正文。对应测试按唯一 event 精确筛选并断言敏感值不出现。 |
| create 下游 builtin `PermissionError` 测试 | PASS | `test_downstream_create_permission_error_has_safe_denied_event` 精确断言 create denied event、operation、`reason=actor_unavailable`、status=401、duration_ms、固定响应、不回显原始 detail，并验证 create service 调用行为。 |
| `SessionActorError`/builtin `PermissionError` 映射 | PASS | Router 先捕获 `SessionActorError`，保留其 reason/status；再捕获 builtin `PermissionError`，使用安全 fallback `actor_unavailable/401`；两者都输出 list/create denied event，响应固定为 `Forbidden` 或 `Session authentication required`。不再误落 generic 500。 |
| denied event/reason/duration | PASS | Router auth gate、Router 下游权限异常、Claude Code Port invalid source/actor unavailable 均具备 event、operation、reason、status、duration_ms；测试按 event 精确筛选。 |
| legacy `actor_present` 与 no-source 兼容 | PASS | legacy no-source/no-user request/success 均根据实际 auth 记录 `actor_present=false`；不要求 principal、不传 auth，旧 service 调用形态保持不变。 |
| local test double `all_but_others` | PASS | 已覆盖当前用户尾部 key 保留、尾部 other 排除、无尾部 user 格式保留及过滤先于分页。 |
| 用户最终过滤规则 | PASS | source 仅 `all_but_others`；只排除尾部 `:user:<other>`；其他格式保留；旧短 key 不使用 request `user_id` 伪造归属；过滤在分页前。 |
| AuthContext/principal seam | PASS（代码结构）/PENDING（真实装配） | actor 只从固定 ASGI scope 的 `AuthenticatedPrincipal` 转换而来，不信任 query/body/header/frame/owner；真实上游 middleware 写入 scope 的部署证据未提供，缺失时 fail closed 为 401。 |
| 精确日志测试 | PASS | legacy success、Router list/create denied、Port invalid source/actor unavailable、Port success/failure、adapter conversion failure 均使用唯一 event 过滤；关键 reason/status/duration 和敏感值不泄露均有断言。 |

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
|------|------|------|
| 正确性 | PASS | 上轮 create key/DTO actor、GET no-source/no-user 兼容、legacy actor_present、Port denied 和下游 PermissionError 误分类问题均已修复。 |
| 安全性 | PASS（代码边界）/PENDING（实际认证装配） | actor 来源可信且 fail closed；权限异常使用固定安全响应；不回显异常正文；旧短 key 和 source 过滤规则无回退。真实认证 middleware 仍需部署侧验证。 |
| 性能 | PASS | source 过滤先于分页，单次遍历；duration 使用 monotonic，无新增 N+1。 |
| 代码风格 | PASS | 未新增 endpoint，未修改 Backend/ProxyPass/Frontend/Claude Gateway TS；改动范围集中。 |
| 测试覆盖 | PASS（本地行为） | focused regression 和新增权限/日志测试全部通过，关键事件使用精确 caplog；正式 ACI 仍 PENDING。 |
| ACI 覆盖率门禁 | PENDING | 当前未提交，暂无稳定 head、ACI XML 或远端 ACI job。 |
| 静态检查 | PASS（无新增生产告警） | Ruff/pyflakes 的 5 个 unused import 和 pycodestyle E306/E303 均为基线已有测试问题；compileall、`git diff --check`、`bash -n` 通过。 |
| 外部系统边界日志（如适用） | PASS（本轮要求） | request/success/failure/denied 主路径已具备 event/reason/status/duration，conversion failure 已低敏化；未发现 token、Cookie、原始 SessionKey、raw session dict 或异常正文落入新增日志。 |

### ACI 覆盖率证据（如适用）

- Base / Head: `380a992040afb93dd9d3f06f4b5c50ba401fb9d7` / `PENDING（工作区改动未提交）`
- 本地 focused 用例: `523/523 (100%)`，skipped=0，failed=0；正式 ACI case pass rate = `PENDING`；threshold = 100%
- 本地 focused 模块总行覆盖率: `905/926 (97.7%，终端显示 98%)`；正式 ACI line coverage = `PENDING`；threshold >= 70%
- 本地按当前 diff 与 coverage JSON 粗略交叉的生产变更行候选: `359/368 (97.6%)`；该数字不是正式 ACI；正式 `changeLineCoverage` = `PENDING`；threshold >= 90%
- 当前本地未覆盖的行主要是 key-format 防御性分支、异常状态构造校验和非本轮主路径辅助分支；本轮新增核心 event/actor/source 行均有行为覆盖。
- 远端 ACI job: `PENDING`。

### 本地验证记录

```bash
cd /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/claude-code-session-source-filter/src/engine
bash scripts/regression_session_source_filter.sh
```

结果：`626 passed, 1 warning`。

```bash
PYTHONPATH=src uv run --with pytest --with pytest-asyncio python -m pytest \
  src/engine/community/api/tests/test_session_router.py \
  src/engine/community/core/adapters/claude_code/tests/test_adapters.py \
  src/engine/community/core/adapters/claude_code/tests/test_adapters_coverage.py \
  src/engine/community/plugins/claude_code/tests/test_mixins_coverage.py \
  src/engine/community/tests/contracts/test_claude_code_local_plugin.py -q
```

结果：`523 passed, 1 warning`，相关生产模块覆盖率 `905/926 = 97.7%`。

```bash
PYTHONPATH=src uv run --with pytest --with pytest-asyncio python -m pytest src/engine/community -q
```

结果：`2500 passed, 5 skipped, 17 warnings`，退出码 0。

## Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
|------|--------|------|------|
| R-01 | source 公开值仅 `all_but_others`/None；非法值 422 | PASS | HTTP/DTO Literal 和 Port/local runtime fail closed；非法 source 不调用 relay。 |
| R-02 | actor 来自可信 AuthContext/principal | PASS（结构）/PENDING（装配） | 固定 ASGI scope 类型检查；不信任业务参数；真实 middleware 装配待部署验证。 |
| R-03 | GET/POST mismatch 403，拒绝不调用 service/relay | PASS | Router 与 adapter 均有 actor 校验，相关测试通过。 |
| R-04 | create key/DTO 使用认证 actor | PASS | key 和 DTO 使用同一 verified actor，request user 缺失场景已覆盖。 |
| R-05 | 旧短 key 不用 request user 伪造归属 | PASS | 旧短 key owner 为 `None`，无 request fallback。 |
| R-06 | 只排除尾部 `:user:<other>`，其他格式保留 | PASS | production/local 均按尾部判断；legacy/旧短/非尾部格式保留。 |
| R-07 | source 过滤先于 offset/limit | PASS | production Port、local double 和 contract tests 均验证。 |
| R-08 | no-source/no-user 保持兼容 | PASS | legacy 分支不要求 principal、不传 auth，service 调用形态保持原状。 |
| R-09 | SessionActorError/builtin PermissionError 下游拒绝映射 | PASS | 专用异常保留 reason/status；builtin fallback 为 actor_unavailable/401；固定安全响应，create/list 测试覆盖。 |
| R-10 | denied event/reason/duration | PASS | Router 和 Port denied 均有 event、operation、reason、status、duration_ms，caplog 精确断言通过。 |
| R-11 | adapter conversion failure event 低敏 | PASS | event/status/reason/duration/key_hash/key_format 已实现，原始 key/raw/error message 不输出，测试通过。 |
| R-12 | local double 合法 source 行为测试 | PASS | current/other/legacy 与过滤前分页均覆盖。 |
| R-13 | ACI 三项门禁 | PENDING | 未提交 head，正式 ACI 未执行。 |

## 具体问题列表

无必须修复问题。

### 非阻塞事项

1. 当前仓库没有真实认证 middleware 将 `AuthenticatedPrincipal` 写入 ASGI scope；部署前需要在实际 composition root/认证链路中验证该 seam。缺失时保持 401 fail closed，不得回退到 query/body actor。
2. 当前没有可由该 feature 可信提供的 request/trace id；代码未伪造该字段。若部署链路已有可信 trace carrier，应在后续集成验证中确认日志格式能带出该关联信息。
3. touched test files 中仍有基线已有 unused import/E306/E303，未由本任务新增；不建议为本任务顺手清理无关测试代码。

## 整体结论

**结论: PASS**

本轮确认上轮所有阻塞项已闭环：

- adapter conversion failure event 已统一为低敏 event/status/reason/duration，并有精确 caplog 测试；
- create 下游 builtin `PermissionError` 已有独立精确测试，固定安全响应和 denied event 正确；
- 专用 `SessionActorError` 与 builtin `PermissionError` 不再误分类为 generic 500；
- legacy actor_present、no-source/no-user 兼容、AuthContext/principal seam、create actor、用户最终过滤规则均无回退；
- focused 和 Engine community 全量测试通过。

### 下一步

- PASS → 继续提交后运行远端 ACI；正式门禁仍须分别满足 casePassRate=100%、lineCoverage>=70%、changeLineCoverage>=90%，并完成真实认证 middleware 的部署验证。
- 当前未执行 commit、push、rebase 或创建 PR。
