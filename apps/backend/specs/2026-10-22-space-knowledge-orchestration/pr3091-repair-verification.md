# PR 3091 本地修复验证（2026-10-25）

## Status

仅完成本地修复与验证，供原评审会话独立复审；本文不代表评审通过。

- Worktree: `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet`
- Branch: `codex/pr3091-p2-repair`
- 修复基线及最终 Git HEAD: `2656a55ab1aa932f0d59d5ee45564eca68c5a70f`
- PR base/merge-base: `980bc488395c7e626eafad704432b4237dd4f5b1`
- 完成前通过 GitHub connector 再次确认 PR Head 未变化。
- 修改全部保持未提交状态，含新文件；没有 staging、commit、push、PR 评论、approve、merge、部署或线上 DDL，也没有向其他会话发送消息。
- 主 checkout `/Users/qiaoyifan/Github/Avernet` 仍为干净的 `dev@29006e1ffd5a281a2ef2672ec99a16a593f113ab`，未修改其他会话的 checkout。

## Root causes and mechanisms

### P2-1 — 不可接收的 immutable head

原实现把存储容量当作接收能力，成员变更成功提交后可能留下超过 2000 人 / 1 MiB 的不可变队头；严格 acknowledged+1 投递不能跳过它。

现在 `_save_snapshot` 在 INSERT 前统一校验成员数和 `json.dumps(body, ensure_ascii=False)` 的 UTF-8 字节数。首次激活超限不留下 outbox；已激活空间的普通/批量成员操作、manual 两个入口和 AUTO 审批与 outbox 一起回滚。抛出安全码 `SPACE_MEMBERSHIP_LIMIT_EXCEEDED`，知识 HTTP 映射 409。诊断只写 scope/revision/count/bytes，不写名单或 payload。

未激活普通空间不受同步配额影响。验证过满员拒绝新增后，remove → rejoin → disabled 仍形成 1/2/3/4 并全部确认；没有跳版本、裁剪、合并或自动 ack。

### P2-2 — SQLite 的 FOR UPDATE 不是 source/snapshot 原子边界

最初只处理 PK 竞争的候选补丁仍会漏掉 first activation vs source mutation：旧 roster 已读、rev1 尚未写时，删除/添加/物理删除/AUTO 可以看到“未激活”而不写 outbox，随后旧名单被永久确认。这不是允许的最终一致性。

最终实现使用共享 `lock_space`：SQLite 在读取 source / roster / 未激活判断之前执行限定 env/live-source 的 no-op UPDATE，取得真实跨连接/进程数据库写 reservation。显式 `gmt_modified = gmt_modified` 避免 onupdate 改动 metadata。随后重新读取 source，已在 guard 前物理删除的源返回不存在。普通、批量维护、manual/AUTO 及物理删除均接入同一边界；server SQL 保留 FOR UPDATE。原先 PK reread 补救已移除，不吞掉 IntegrityError 或锁超时，不修改全库事务模式，不加入进程锁或无界重试。事务内没有新增网络调用。

### P2-3 — SQLite rowid 与保留 outbox 身份寿命不同

物理删除最大 rowid 后，SQLite 可复用 ID，但旧 outbox/disabled 仍以 env+space_id 保留。

SQLite 创建 Space 的初次 INSERT 已持有数据库写锁；在写任何成员、外部副作用或返回 ID 前，由独立 helper 把候选 ID 保留在同环境历史 outbox 最大 ID 之上。TEAM、PERSONAL 和 additive SpaceSkillRepository.create_space 都覆盖。索引 lookup 使用现有 PK 前缀，不读取历史 payload，不清理旧 pending disabled。其他 dialect 保持原生 ID 分配。

## RED → GREEN

| 阶段 | 已实际执行的结果 | 证据 |
| --- | --- | --- |
| 原 Head 上首批 11 个真实数据库回归 | **10 failed, 1 passed** | `/tmp/pr3091-red.log`：超限仍提交、真实双连接 PK IntegrityError、old_id=new_id |
| additive 创建入口补充 RED | **1 failed, 15 deselected** | `/tmp/pr3091-additive-red.log`：同样复用 old_id |
| 对初版 PK 补丁的原子性 RED | **4 failed, 1 passed** | `/tmp/pr3091-activation-red.log`：remove/add/delete/AUTO 丢事件 |
| 最终两个真实数据库回归文件 | **24 passed** | `/tmp/pr3091-activation-final.log` |
| 最终受影响扩展套件 | **1502 passed, 50 warnings, 0 failed** | `/tmp/pr3091-final-combined.log`，125.26s |
| 最终共享 knowledge-bases HTTP runner | **6 passed, 1618 deselected** | `/tmp/pr3091-endpoints-final.log`，1.71s |
| 全部变更 Python 文件 Ruff check | **passed** | 未修改 lint/CI 配置 |
| 全部变更源码与测试的 1000 行约束 | **passed** | 最大源码 982 行；无豁免 |
| git diff --check | **passed** | 含 untracked 文件另按字节/行数检查 |

24 个定向回归已包含在 1502 中，不能重复累计。警告主要为已有 Pydantic/Starlette deprecations 与 SyntaxWarning。此前 1495 passed 的中间结果早于最终 guard，不作为最终版本证明。

## Reproduction commands

所有下面的 pytest 命令均在本任务的 backend 工作目录执行（初次 RED 使用相同目录对应文件的仓库根相对路径）。

```bash
cd /Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend
PY=/Users/qiaoyifan/.codex/worktrees/tc-space-kb-create-avernet/apps/backend/.venv/bin/python
export PYTHONPATH="$PWD/src:$PWD"

# 最终真实数据库定向回归（24 passed）
"$PY" -m pytest \
  tests/community/core/space_knowledge/test_membership_activation_races.py \
  tests/community/core/space_knowledge/test_membership_real_database.py \
  -q --tb=short

# 最终合并验证（1502 passed），保持原 pytest collection 模式
"$PY" -m pytest \
  tests/community/core/space_knowledge \
  tests/community/contracts/test_space_knowledge.py \
  tests/community/adapters/http/space_knowledge \
  tests/community/e2e/test_space_knowledge_flows.py \
  tests/community/core/spaces tests/community/core/work_orders \
  tests/community/adapters/http/spaces_management \
  tests/community/adapters/http/spaces_internal \
  tests/community/adapters/http/work_orders \
  tests/community/adapters/http/openapi_v1/work_orders \
  tests/community/repository/skill_center \
  tests/community/architecture tests/community/framework \
  -q --tb=short

# 原共享 HTTP runner（6 passed）
"$PY" -m pytest tests/community/endpoints/test_endpoint_runner.py \
  -k knowledge-bases -q --tb=short
```

变更文件 Ruff 与行数使用 `git diff --name-only` 加 `git ls-files --others --exclude-standard` 的并集，过滤 `.py` 后逐一运行 `python -m ruff check`、计数 `splitlines()`；因此包含未跟踪的新文件。`git diff --check` 加新文件 whitespace 检查。没有改 CI 门槛、collection 模式、coverage 分母或豁免。

环境：Python 3.12.13；复用环境对应 checkout 的 uv.lock 与本 Head 相同，SHA1 `e5f5305792ab911e68208fea0c6ae0baa82829e3`。实际安装 pytest 9.1.1、pytest-asyncio 1.4.0、SQLAlchemy 2.0.51、FastAPI 0.138.2、HTTPX 0.28.1、Pydantic 2.13.4、Ruff 0.15.15、injector 0.24.0 均逐项与当前 lock 核对一致。实际导入的生产源码来自本工作树，不是环境所在的另一个 checkout。没有声称整个复用环境是全新安装或完全没有额外包。

格式说明：新增文件和变更的 knowledge_sync/router/HTTP test 通过 format check；其余既有大文件保持原格式，不为了 formatter 重排无关代码。完整 changed-file Ruff **check** 通过，不宣称所有既有文件 formatter-clean。

## Verification boundary and deployment risks

- 回归使用真实文件 SQLite 的 CommunityDatabase，包含独立连接和两个独立 Python 进程；没有用 fixture RLock 证明并发正确性。外部接收能力仍以只校验 2000 人/1 MiB 的受控 sink 替换，不是远端 DTO/网关全栈验证。
- 本轮未重跑全 backend 约 25k tests、corporate assembly、Singlebox coverage、changed-line coverage 或完整 pre-push SAST/secret gate；只报告上述已实际运行的 1502+6，不复用 PR 原作者的旧结果作为本轮证明。
- 没有测试 MySQL/OceanBase 实例、真实远端接收端/SSO/gateway、实际部署或在线迁移；不能声称已证实远端数据泄漏。
- 不改 ac_space/ac_space_member/outbox/shared queue 的 schema、model 或 index。原 outbox 建表迁移仍须先于所有 hooks / SQLite 创建入口部署；没有执行 DDL。
- 新的安全 error code 为增量契约；接收端 DTO、digest/revision、queue pointer、ACL 所有权均不变。SQLite guard 增加写锁持有时间，受数据库 busy timeout 约束，可能增加竞争失败；不通过吞错返回成功。
- 若旧候选已经写入无法接收的历史或复用了身份，须显式停写、核对 source/provider 后协调处理；此修复不跳过、重写或删除旧 pending disabled。
- 至少永久保留每个 retired outbox scope 的最后一行，维持身份墓碑；全部写入口须一起升级。直接 SQL/旧版本绕过 hooks 不在保证内。回滚旧 writer 会重开缺陷，须暂停相关写入后操作。
- 后续成员撤权仍为最终一致；服务不可用会延长延迟。这与本轮修掉的“无任何 pending、永久漏记”不同。

## Complete changed files

- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/specs/2026-10-22-space-knowledge-orchestration/contract.md`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/specs/2026-10-22-space-knowledge-orchestration/pr3091-repair-verification.md`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/adapters/http/space_knowledge/router.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/core/repository/implementations/skill_center/space_skill.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/core/repository/implementations/spaces/identity.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/core/repository/implementations/spaces/knowledge_sync.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/core/repository/implementations/spaces/management.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/core/repository/implementations/spaces/space.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/core/repository/implementations/work_orders/auto_approval.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/core/repository/implementations/work_orders/work_order.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/core/space_knowledge/README.md`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/src/agentclaw/community/plugin_api/space_membership.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/tests/community/adapters/http/space_knowledge/test_space_knowledge_router.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/tests/community/core/space_knowledge/test_membership_activation_races.py`
- `/Users/qiaoyifan/.codex/worktrees/pr3091-p2-repair/Avernet/apps/backend/tests/community/core/space_knowledge/test_membership_real_database.py`
