# Task Artifact Domain 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec 在 task 模块落地产物领域对象(TaskArtifact):relay 回调上报产物列表、集中/relay 写点双轨发布、`saved_graph` 同事务落表、读侧最小可见。

**Architecture:** 纯领域模型(kind-tagged 内容分支 + 不可变清单)→ 复用 callback-audit 的 contextvar 暂存机制,产物行随 `save_graph` 同事务内嵌写入(select-first 查重,无唯一键);读侧仅 `TaskNodeDTO.artifacts` 富化 + 轨迹 HTML 产物块;集中 push/pull/BBS 协议不动,只走文本双轨派生。

**Tech Stack:** Python 3.12(仓库 `apps/backend`,uv 管理)、SQLAlchemy(Base metadata / OceanBase-MySQL DDL 双轨)、pytest、pydantic(relay 报文 DTO)。

**Spec:** `apps/backend/specs/2026-10-20-task-artifact-domain/spec.md`(计划从 spec 立论;执行者 spec 与本计划都读)

## Global Constraints

- 测试一律 `uv run pytest ...`,** cwd = apps/backend**(首次先 `uv sync`;本机尚无 `apps/backend/.venv`);ruff changed files 清洁。
- `pytest.ini`:`testpaths = tests`、`--ignore=tests/community/acceptance`(验收道 `RUN_ACCEPTANCE=1` 不进本计划)。
- 架构纪律(违反=任务不通过):Rule 22「HTTP adapter 只转协议」;core/repository 四条纪律(Protocol 全 `@abstractmethod`、实现声明 Protocol、protocols 无运行时域导入、协议与实现不同文件)——`core/repository/README.md` provides 表 + Boundary YAML 必须同 commit 增补,守卫测试 `tests/community/architecture/test_repository_contracts.py`、`test_task_module_responsibility_guards.py` 必须保持绿。
- 产物 ID:`"task-art-" + uuid.uuid4().hex[:16]`;内容形态枚举封闭 `text|file|url_link|code`,未知值 fail-loud(`TaskArtifactContentError`,`core/task/domain/errors.py`)。
- 产物字段里**禁止**出现:临时下载 URL、presign、token、object handle、宿主机绝对路径(`FileContent.storage_id` 的 BOT_WORKSPACE 值是工作区**相对**路径,校验拒绝对路径/`..` 逃逸/长度>512)。
- 表结构、列空性分组、索引、幂等语义以 spec §6 DDL + §5.3 为准(无唯一键,select-first 查重,`node_id/attempt/content_hash/creator/modifier` 列可空但发布路径必填)。
- 不动集中 push/pull/BBS 报文协议;不改 `_relay_output_value`/relay 聚合口径;不新建 HTTP 端点。
- 每次 commit 消息 `feat(task-artifact): ...` / `test(task-artifact): ...` / `chore(task-artifact): ...`,结尾带 `Co-Authored-By: Claude <noreply@anthropic.com>`。

## Review Focus

实现时最可能咬人的五类输入/失效模式(spec 隐含但单任务测试不会天然覆盖),各自钉在所属任务的步骤里:

1. **查重键含 NULL**:`node_id/attempt/content_hash` 为 NULL 的行(人工修数残留)——select-first 不得崩溃、不得误判重,**直接落新行**(Task 4 测试)。
2. **DECLINED 事件携带 artifacts**:rela 上报方在拒绝事件里夹带产物——必须整体拒绝,不是静默丢弃(Task 6 测试)。
3. **attempt 交叉校验失配**:报文 item 的 attempt 与图上 `harness_retries` 不一致——拒绝整个回调,不得落任何产物行(Task 7 测试)。
4. **无 artifacts 字段的旧报文**:rela 老客户端、以及集中 push/pull 全量存量路径——行为逐字节不变(Task 6/7 各有基线测试)。
5. **`artifact_kind` 与非空 content 字段不一致 / 四个 content 全空 / 多个非空**:解析层拒绝,不猜分支(Task 6 测试)。

---

### Task 1: 环境引导 + 领域模型与内容序列化

**Files:**
- Create: `src/agentclaw/community/core/task/task_context/task_artifact/__init__.py`(re-export 公共名)
- Create: `src/agentclaw/community/core/task/task_context/task_artifact/models.py`
- Modify: `src/agentclaw/community/core/task/domain/errors.py`(+`TaskArtifactContentError`)
- Create: `tests/community/core/task/task_artifact/__init__.py`(空)
- Test: `tests/community/core/task/task_artifact/test_artifact_models.py`

**Interfaces(Produces,后续任务全部依赖):**
- `class ArtifactKind(StrEnum)`: `TEXT="text"`, `FILE="file"`, `URL_LINK="url_link"`, `CODE="code"`
- `class StorageType(StrEnum)`: `BOT_WORKSPACE="bot_workspace"`, `SESSION_FILE="session_file"`
- `@dataclass(frozen=True) class ArtifactScope`: `session_id: str | None`, `task_id: str`, `node_id: str`, `attempt: int`
- `@dataclass(frozen=True) class TextContent`: `text: str`
- `@dataclass(frozen=True) class FileContent`: `storage_type: StorageType`, `storage_id: str`, `file_type: str`, `file_name: str`, `file_id: str | None`
- `@dataclass(frozen=True) class UrlLinkContent`: `url: str`, `type: str`
- `@dataclass(frozen=True) class CodeContent`: `code_base_name: str`, `code_base_address: str`, `pr_link: str`, `pr_desc: str`;基类 `ArtifactContent`(frozen dataclass,无字段)
- `@dataclass(frozen=True) class TaskArtifact`: `artifact_id: str`, `artifact_name: str | None`, `kind: ArtifactKind`, `scope: ArtifactScope`, `content: ArtifactContent`, `content_hash: str`, `creator: str`, `created_at: int`;`@property storage -> str`(FILE→`storage_type.value`,否则 `"inline"`)
- `content_to_json(content: ArtifactContent) -> str`:kind-tag 前缀 canonical JSON(`{"kind": tag, **fields}`,`sort_keys=True, separators=(",", ":")`)
- `content_from_json(payload: str) -> ArtifactContent`:按 tag 选构造类,未知 tag → `TaskArtifactContentError`
- `artifact_content_hash(content: ArtifactContent) -> str`:`sha256(content_to_json(content)).hexdigest()`
- `artifact_text_value(output: dict[str, Any]) -> str | None`:单键 `result`/`markdown`/`output` 且值为非空字符串 → 该字符串,否则 None
- `canonical_json(value: Any) -> str`:控制信号用(`sort_keys=True, separators=(",", ":")`)
- `new_artifact_id() -> str`

- [ ] **Step 1: 环境引导**(一次)`uv sync`;`uv run pytest tests/community/core/task -q` 记录基线通过数(后续任务以此为对照,不苛求存量全绿,但不得新增失败)。

- [ ] **Step 2: 写失败测试** `test_artifact_models.py`,至少覆盖:
  - 构造校验:FileContent BOT_WORKSPACE 缺 `storage_id` / 带 `file_id` → `TaskArtifactContentError`;SESSION_FILE 缺 `file_id` → 报错;`storage_id` 绝对路径 / 含 `..` / 超长 → 报错;UrlLinkContent 非 http(s)、`type` 空、超 64 → 报错;CodeContent `pr_desc` 超 8KB → 报错(允许空串)
  - `content_to_json`/`content_from_json` 四分支 roundtrip 相等;未知 tag `"kind":"bogus"` → `TaskArtifactContentError`
  - `artifact_content_hash` 对同一内容稳定、对字段序不同但内容相等的两个 dict 形分支输入一致(canonical 保证)
  - `artifact_text_value`:`{"output":"done"}`→`"done"`;`{"markdown":"# x"}`→`"# x"`;`{"output":"a","b":1}`→None;`{"skipped":True}`→None
  - `TaskArtifact.storage`:FILE 分支=storage_type 值,TEXT 分支=`"inline"`
  - `new_artifact_id()` 前缀 `task-art-`、长度 16 hex

- [ ] **Step 3: 跑测试确认失败** `uv run pytest tests/community/core/task/task_artifact/test_artifact_models.py -q` → FAIL(ImportError)。

- [ ] **Step 4: 实现** `task_artifact/models.py`:全部类型 + `__post_init__` 校验(spec §3.4 规则逐条:长度上限 url/pr_link/code_base_address=2048、type=64、pr_desc=8KB、file path=512);`domain/errors.py` 加 `TaskArtifactContentError`;`__init__.py` re-export。

- [ ] **Step 5: 跑测试确认通过** 同 Step 3 命令 → PASS。

- [ ] **Step 6: 全量对照** `uv run pytest tests/community/core/task -q` 不低于 Step 1 基线;`uv run ruff check src/agentclaw/community/core/task/task_context/task_artifact/ tests/community/core/task/task_artifact/`。

- [ ] **Step 7: Commit** `feat(task-artifact): artifact domain models with kind-tagged content branches`

### Task 2: 建表 SQL + ORM + Record

**Files:**
- Create: `src/agentclaw/community/core/task/sql/2026_10_20_task_artifact.sql`
- Modify: `src/agentclaw/community/core/task/repository/models.py`(+`TaskArtifactModel`)
- Modify: `src/agentclaw/community/core/task/repository/types.py`(+`TaskArtifactRecord`)
- Test: `tests/community/repository/task/test_task_artifact_models.py`(新文件;复用该目录 conftest 的 in-memory SQLite `DatabasePlugin` stub)

**Interfaces:**
- Consumes: Task 1 全部类型。
- Produces: `@dataclass(frozen=True) class TaskArtifactRecord`(table-faithful):`artifact_id, artifact_name: str|None, task_id, node_id: str, attempt: int|None, scope_session_id: str|None, kind: str, storage: str, content: ArtifactContent, content_hash: str|None, creator: str|None, modifier: str|None, created_at: int(ms,读时由 gmt_create 折算), gmt_create: datetime`;`TaskArtifactModel.to_record() -> TaskArtifactRecord`(content 经 `content_from_json` 解析成领域对象)。

- [ ] **Step 1: 写失败测试**:SQLite `Base.metadata.create_all` 含 `task_artifact`;插入一条四分支各异的样例行 → `to_record()` 字段/content 对象相等;`content_hash`/`node_id`/`attempt` 置 NULL 的行可插可读(空值容错口)。
- [ ] **Step 2: 跑测试确认失败**(ImportError/建表缺表)。
- [ ] **Step 3: 实现**:SQL 文件**逐字复制 spec §6 的权威 DDL**(头注释+全部列;不增删);ORM `TaskArtifactModel` 按仓库 house style(Base/`_binary_string`/`to_record()`;列名含 `scope_session_id`/`gmt_modify`——注意本表沿用的是 `gmt_modify` **非** `gmt_modified`,与 DDL 对齐,别踩 trajectory 的历史漂移坑);`types.py` 记录类,frozen。
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(task-artifact): task_artifact table DDL, ORM model and record type`

### Task 3: 读侧仓储协议 + 实现 + DI/README 注册

**Files:**
- Create: `src/agentclaw/community/core/repository/protocols/task/artifact.py`
- Create: `src/agentclaw/community/core/repository/implementations/task/task_artifact_repository.py`
- Modify: `src/agentclaw/community/core/repository/protocols/task/__init__.py`(re-export)
- Modify: `src/agentclaw/community/core/repository/README.md`(provides 表 + Boundary YAML)
- Modify: `src/agentclaw/community/di/modules/task_persistence_module.py`(bind singleton)
- Test: `tests/community/repository/task/test_task_artifact_repository.py`

**Interfaces:**
- Consumes: `TaskArtifactRecord`(Task 2)。
- Produces: `class TaskArtifactRepositoryProtocol(Protocol)`,仅读:
  - `list_by_task(self, task_id: str, *, limit: int = 200) -> list[TaskArtifactRecord]`
  - `list_by_task_node(self, task_id: str, node_id: str, *, limit: int = 100) -> list[TaskArtifactRecord]`(attempt DESC, gmt_create DESC)
  - 实现 `TaskArtifactRepository(TaskArtifactRepositoryProtocol)`,`@inject def __init__(self, db: DatabasePlugin)`,body 用 sibling 仓储同款 `orm_session()`/flush 习惯。

- [ ] **Step 1: 写失败测试**:插 3 行(不同 attempt/kind)→ `list_by_task_node` 返回 attempt 倒序;`list_by_task` 汇全部 node;`limit` 生效。
- [ ] **Step 2: 确认失败;Step 3 实现协议+实现类+`__init__` re-export+DI bind。**
- [ ] **Step 4: 纪律守卫**:`uv run pytest tests/community/architecture/test_repository_contracts.py tests/community/repository/task -q` 全绿(README provides 增补就位,否则 provides 守卫测试红)。
- [ ] **Step 5: Commit** `feat(task-artifact): read-only task artifact repository protocol, impl and DI wiring`

### Task 4: `save_graph` 契约扩展——产物行同事务内嵌写

**Files:**
- Modify: `src/agentclaw/community/core/repository/protocols/task/core.py:335`(`save_graph` 签名)
- Modify: `src/agentclaw/community/core/repository/implementations/task/task_graph_repository.py:235`
- Modify: 所有 `save_graph` 测试替身(`grep -rn "def save_graph" tests/ src/`,逐个加同默认参,不动行为)
- Test: `tests/community/repository/task/test_task_graph_repository.py`(扩展既有文件)

**Interfaces:**
- Consumes: `TaskArtifactRecord`。
- Produces: `save_graph(..., published_artifacts: list[TaskArtifactRecord] | None = None)`(keyword-only,默认 None=行为不变);产物行在**同一 `with self._transaction()`** 内写入,语义:
  - select-first:按 `(task_id, node_id, attempt, kind, content_hash)` 五元等值 SELECT,命中→跳过该行;**任一键为 NULL→ 直接插入**(NULL 不参与等值比较,Review Focus #1)
  - 插入映射到 `TaskArtifactModel`(列平凡映射,`gmt_create` 用 `datetime.fromtimestamp(record.created_at/1000)`,`modifier=record.creator`,`storage=record.storage`)
  - 产物写异常 → 让异常穿出(整个事务连带图写回滚),**不吞、不降级**

- [ ] **Step 1: 写失败测试**:(a)`save_graph(published_artifacts=[r])` → 图与产物同事务落库;(b)同键二连调→ 第二次不再插(查重);(c)`content_hash=None` 的行两次调用→ 两行都在(NULL 豁免查重);(d)stub 迫使产物 INSERT 抛异常(如 content JSON 非法)→ 整事务回滚,图版本未推进。
- [ ] **Step 2: 确认失败。**
- [ ] **Step 3: 实现**:协议签名 + 仓储内嵌写(参照同文件 `_upsert_callback_audit` 的先例位置与写法);grep 更新测试替身。
- [ ] **Step 4: 通过 + `uv run pytest tests/community/repository/task tests/community/core/task/test_task_module_responsibility_guards.py -q` 全绿。**
- [ ] **Step 5: Commit** `feat(task-artifact): save_graph persists published artifact rows in the same transaction`

### Task 5: 上报解析——`ArtifactReportItem` + relay DTO 扩展

**Files:**
- Create: `src/agentclaw/community/core/task/task_context/task_artifact/report_item.py`
- Modify: `src/agentclaw/community/core/task/domain/models.py:465`(`TaskNodePatch` + 字段)
- Modify: `src/agentclaw/community/adapters/http/task/schemas_relay_report.py:32`(`ExecutionEventDTO` + 新 DTO)
- Test: `tests/community/core/task/task_artifact/test_artifact_report_item.py`

**Interfaces:**
- Consumes: Task 1 内容分支类。
- Produces:
  - `@dataclass(frozen=True) class ArtifactReportItem`: `artifact_name: str|None`, `artifact_kind: str`, `session_id: str|None`, `task_id: str`, `node_id: str`, `attempt: int`, `content: ArtifactContent`
  - `parse_artifact_report_item(raw: dict, *, report_task_id: str, report_node_id: str) -> ArtifactReportItem`:结构性校验全在此——四 content 字段恰好一个非空、`artifact_kind` 与非空字段分支一致、content JSON 解析+分支构造、`raw["task_id"]==report_task_id`、`raw["node_id"]==report_node_id` 失败→ `raise ValueError`(HTTP 层转 4xx 既有错误信封)
  - `TaskNodePatch.artifact_patch: list[ArtifactReportItem] | None = None`(None=未上报;注释指向 report_item 模块,domain 包不新增 import,类型注解用 `list | None`)
  - `class ArtifactReportItemDTO(BaseModel)`(schemas_relay_report.py,`extra="forbid"`):十字段(`artifact_name/artifact_kind/session_id/task_id/node_id/attempt/text_content/file_content/url_link_content/code_content`,content 字段为 JSON **字符串**);`ExecutionEventDTO` 增 `artifacts: list[ArtifactReportItemDTO] | None = None`;`to_domain` 组装 `TaskNodePatch(artifact_patch=[parse_artifact_report_item(...)])`
- 校验规则(Review Focus #2/#5):decline 检查、全空/多非空、kind 不一致。

- [ ] **Step 1: 写失败测试**:spec §4 三例(file×2 两种 storage_type、url_link、code)合法解析;`attempt` 非法值、`task_id` 与报文不符、两 content 并存、kind=text 但 file_content 非空、`artifact_kind="bogus"`、content 字符串非法 JSON → ValueError;`[{"..." }]` 超 64 条整批拒绝(上限校验放 `parse_artifact_report_items(items: list) -> list[ArtifactReportItem]` 列表级包装)。
- [ ] **Step 2: 确认失败;Step 3 实现 parse 函数 + DTO + patch 字段。**
- [ ] **Step 4: 通过 + 回归 relay 报文既有契约测试**(`uv run pytest tests/community -k relay_report -q`)——无 `artifacts` 字段的报文逐字节不变(Review Focus #4)。
- [ ] **Step 5: Commit** `feat(task-artifact): flat self-describing artifact report items on the relay contract`

### Task 6: 发布 capture 服务 + contextvar 暂存

**Files:**
- Create: `src/agentclaw/community/core/task/task_context/task_artifact/artifact_publish.py`
- Modify: `src/agentclaw/community/di/modules/task_module.py`(provider:`TaskArtifactPublishService` singleton,`_resolve_optional_port` 同款 try/except;未绑定→ warning 一次,不能用→不发布)
- Test: `tests/community/core/task/task_artifact/test_artifact_publish.py`

**Interfaces:**
- Consumes: Task 1(`artifact_text_value/canonical_json/new_artifact_id/artifact_content_hash/TaskArtifactRecord 构造材料`)、Task 2(`TaskArtifactRecord`)、Task 5(`ArtifactReportItem`)。
- Produces:
  - contextvar helpers:`stage_published(records: list[TaskArtifactRecord]) -> None`;`drain_published() -> list[TaskArtifactRecord]`(取走并清空)
  - `class TaskArtifactPublishService`: `capture_node(graph, node, *, artifact_patch: list[ArtifactReportItem] | None, now_ms: int) -> list[TaskArtifactRecord]`(纯函数式:构造记录**并** stage),规则:
    - 文本双轨:`artifact_text_value(fold 后 run_info.output)` 命中→一条 TEXT 记录;未命中且 output 含 `skipped`/`notify_result` 键→ 一条 TEXT 记录(text=canonical_json(整 output dict));每 attempt 两类各至多 1 条
    - `artifact_patch` 逐条→对应 kind 记录(kind 映射 file/url_link/code/text)
    - `scope`:session_id 图绑定优先(根节点 `run_info.extend_props["session_id"]`,取 `graph.tasks[0].run_info.extend_props`),未绑定采纳报文值;attempt=该节点 fold 后 `extend_props["harness_retries"]`(int,缺省 0);**报文 attempt ≠ 图上快照 → `TaskArtifactContentError`**(Review Focus #3)
    - `creator` = `run_info.assignee or "system"`;`artifact_name` 回落规则:file→`file_content.file_name`,其余→上报值 or None;`content_hash` 必填(发布路径)
    - `storage` 由 TaskArtifactRecord 携带(Task 2)

- [ ] **Step 1: 写失败测试**:fold 后 output 单键文本→1 条 TEXT;`{"skipped":true}`→1 条 TEXT canonical JSON;同一 attempt 文本+信号并存→2 条;带 artifact_patch(file×2+url_link+code)→4 条记录、五元组/名称回落正确;attempt 失配→ 报错;assignee 缺失→ creator="system";stage→drain 幂等清空。
- [ ] **Step 2: 确认失败;Step 3 实现(纯构造 + contextvar,不碰 repo)。Step 4: 通过。**
- [ ] **Step 5: Commit** `feat(task-artifact): publish capture service with contextvar staging`

### Task 7: 写点挂接——集中 fold + relay 原子闭包 + `_persist_locked` 消费

**Files:**
- Modify: `src/agentclaw/community/core/task/task_context/task_graph_service.py`(`update_task_node_info` fold 后 capture `:658` 附近、`update_task_graph_info` `:814` 附近、`_persist_locked` `:199` drain→`save_graph(published_artifacts=...)`)
- Modify: `src/agentclaw/community/core/task/task_context/relay_report.py`(`apply_relay_report` EXECUTION_RESULT `:250` 与 completion `:308` 后 capture;DECLINED 携带→ 既有非法事件分支)
- Test: `tests/community/core/task/task_artifact/test_artifact_dualwrite.py`、`test_artifact_relaywrite.py`

**Interfaces:**
- Consumes: Task 4(`save_graph` 新参)、Task 5(`TaskNodePatch.artifact_patch`)、Task 6(capture/stage/drain)。
- Produces: 无新接口;行为契约——
  - 集中通道:`update_task_node_info`/`update_task_graph_info` 变更闭包内 fold 之后调用 capture(重放安全:闭包重复执行由 select-first 吸收);`_persist_locked` 在 `save_graph(...)` 调用时把 `drain_published()` 结果作为 `published_artifacts` 传入;`graph_repo is None`(内存-only 提前返回分支)→ drain 丢弃 + debug 日志一条
  - relay:`apply_relay_report` 在 EXECUTION_RESULT accepted 写点与 completion 写点 capture(mutate 闘包内);事件 `execution_decision != "ACCEPTED"` 却携带 artifacts → `TaskArtifactContentError`(沿 relay_report.py 既有的报文异常处理分支——grep 该文件中现有 `raise` 对 report 契约错误的处理方式并照同样的出口,不新造错误通道)

- [ ] **Step 1: 写失败测试**(双轨,全用内存图 + SQLite stub graph repo):
  1. 集中:text-only patch → output 原样 + 1 条 TEXT 落库
  2. 重试:`harness_retries` 增量后旧 attempt 记录仍在、新 attempt 新行
  3. 版本重放:制造 `GraphVersionConflictError` 一次重放 → 产物不重复(Review Focus 关联 §5.3)
  4. relay:task_service_relay 层面——ACCEPTED + artifacts(file+code)→ 2 行;DECLINED+artifacts → 拒绝且无任何行
  5. graph repo 未接线 → 不发布不炸
  6. 回归:无 artifact_patch 的既有 update_task_node_info/relay 全套测试不变绿(Review Focus #4)
- [ ] **Step 2: 确认失败;Step 3 实现(注意 capture 在 `_mutate_with_version_retry` 闭包内、drain 在 `_persist_locked` 提交前——对齐 callback_audit contextvar 的生命周期写法)。Step 4: 通过。**
- [ ] **Step 5: 全量** `uv run pytest tests/community/core/task tests/community/repository/task -q` ≥ 基线 + 新测试全绿;`uv run ruff check <changed>`。
- [ ] **Step 6: Commit** `feat(task-artifact): dual-track artifact publication wired into central fold and relay mutation`

### Task 8: 读侧最小暴露——Descriptor DTO + 看板富化 + 轨迹 HTML

**Files:**
- Modify: `src/agentclaw/community/adapters/http/task/schemas.py`(+`TaskArtifactDescriptorDTO`;`TaskNodeDTO` `:305` + `artifacts`;`graph_to_dto` `:690` + 可选参 `artifacts_by_node: Mapping[str, list[TaskArtifactRecord]] | None = None`,None→输出零变化)
- Modify: `src/agentclaw/community/adapters/http/task/router.py`(dashboard 路由注入 `TaskArtifactRepositoryProtocol`,组 mapping 传入;openapi_v1/task/router.py `:111/:135` 同)
- Modify: `src/agentclaw/community/adapters/http/task/trajectory_html.py`(「产物」块:Descriptor 渲染 `artifact_name or file_name`,**不出任何 URL**)
- Test: `tests/community/adapters/http/task/` 下既有 dashboard/trajectory 测试扩展 + 新 `test_task_artifact_readside.py`

**Interfaces:**
- Consumes: `TaskArtifactRepositoryProtocol.list_by_task`(Task 3)。
- Produces: `class TaskArtifactDescriptorDTO(BaseModel)`: `artifact_id: str`, `artifact_name: str|None`, `kind: str`, `content: dict`(kind-tagged wire 形,`content_to_json` 后再 json.loads 或等价), `attempt: int|None`, `creator: str|None`, `created_at: int`;`TaskNodeDTO.artifacts: list[TaskArtifactDescriptorDTO] | None = None`。

- [ ] **Step 1: 写失败测试**:(a)落 2 产物→ dashboard 路由响应对应 node `.artifacts` 为 2 项、字段齐;(b)`graph_to_dto(graph)` 不传新参 → 输出与改前逐字节相同(Review Focus #4);(c)轨迹 HTML 含产物名、断言全文无 `http` 链接出现于产物块;(d)attempt 倒序。
- [ ] **Step 2: 确认失败;Step 3 实现**(HTTP 层只做读端口编排 + DTO 翻译,Rule 22:业务判断零新增);**Step 4: 通过 + openapi 快照**——运行涉及 openapi 导出/契约快照的测试(`grep -rln "openapi" tests/community/adapters/http/openapi_v1/task/`),快照含新 optional 字段则同 commit 更新快照文件。
- [ ] **Step 5: Commit** `feat(task-artifact): dashboard node DTO artifact enrichment and trajectory artifact block`

### Task 9: 全量回归 + spec 状态收口

**Files:**
- Modify: `apps/backend/specs/2026-10-20-task-artifact-domain/spec.md`(头部:「状态」DRAFT→已实现待评审;「验证状态」补测试证据:各 suite 通过数)
- Test: 无新增;跑全量门禁。

- [ ] **Step 1: 门禁**:`uv run pytest tests/community/core/task tests/community/repository/task tests/community/adapters/http/task tests/community/architecture -q` 全绿(不低于 Step 基线);`uv run ruff check src/agentclaw/community/core/task/task_context/task_artifact/ src/agentclaw/community/core/repository/ src/agentclaw/community/adapters/http/task/ tests/community/core/task/task_artifact/ tests/community/repository/task/`。
- [ ] **Step 2: 反目标自查**(对照 spec §1.4):产物 content JSON 里 grep 确认无 `presign|X-ECB-Token|object_handle|/Users/` 字样;集中 push/pull 报文 schema 无改动(`git diff --stat` 校验 `schemas.py` 的 push/poll body 模型零变化)。
- [ ] **Step 3: spec 状态行更新 + Commit** `chore(task-artifact): mark spec implemented with verification evidence`

---

## Self-Review 记录

- Spec 覆盖:§3/§4→Task 1/5;§5→Task 4/6/7;§6→Task 2/3/4;§7→Task 8;§9 验收门禁→Task 8/9 步骤内逐条对应(BBS 协议不动、无 artifacts 报文不变、HTML 无 URL 等均入测试)。§1.3 非目标(不搬字节/不做下载接口/不动存量写方)由 Global Constraints 管住。
- 类型一致性:`TaskArtifactRecord`/`ArtifactReportItem`/`save_graph(published_artifacts=...)`/`list_by_task(_node)` 在各任务 Interfaces 块中签名一致。
- 权衡:Task 7 把集中与 relay 挂接并在一个任务,因两者共用 drain/生命周期机制、评审为一个原子门禁;其余任务各自独立可评审。