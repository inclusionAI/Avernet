# Bot Config Manifest 实现执行计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 设计来源:`docs/superpowers/specs/2026-08-31-bot-config-manifest-design.md`(§4.1–4.10 逐项设计)。本计划分两个深度层,刻意为之:
> - **Phase 0/A/B/C 到代码级**(W1/W2/W3——立即开工的前三项,含完整代码与命令);
> - **Phase D–J(W11→W4→W5→W6→W8→W7→W9)到文件/test 清单/gate 级**——每项开工时按 §0.4 落自己的 `specs/<开工日>-<slug>/tasks.md` 细化为代码级(writing-plans 对该子项再走一轮)。这是仓库 SDD 惯例(work-items §8)与"字段/锚点要对当时已合并的依赖拍稳"共同要求的,不是偷懒:W4 的物化器接口形状由 W2/W3 的实际交付决定,预写代码只会按假想形状写出 #935 修过的那种坏 fixture。

**Goal:** 按 `W1→W2→W3→W11→W4→W5→W6→W8→W7→W9` 单人序列交付 bot-config-manifest 的 10 个工作项,每项一个 PR、各自关闭对应 issue。

**Architecture:** 新模块 `core/bot_config_manifest/`(schema/能力/服务/fetch/物化器)+ 4 张新表(`ac_bot_config_manifest`/`ac_source_credential`/`ac_manifest_object`/`ac_manifest_apply`)+ openapi_v1 新路由(ADMISSION 注册)+ 平台侧 apply(GitOps 收敛、类目覆盖、两阶段)。交付层零新增:teclaw 走既有 `BotConfigArtifact` 组装,ARCA 走既有 push/NAS 通道,script 走 #935 启动链。

**Tech Stack:** Python 3.12 / FastAPI + 现有 `Injected` DI / SQLAlchemy ORM / httpx(社区 core 已在用,同款客户端选型)/ pytest(`tests/community`,镜像 src 结构)。

> **修订(2026-08-31,用户拍板)**:**一期所有配置下发全部在 bot 启动/激活之后**——启动前的三件事(teclaw 首份 artifact 含 manifest、创建流程内 apply、W4-A 阶段挂 `_build_create_bot_payload`)推第二期,#1508 跟踪。直接改动:Phase E(两阶段形状保留但一期无挂接方)、**Phase H 大幅收缩**(不碰 `TeclawProvisionService`,teclaw 臂=ACTIVE 后逐文件通道,W12 不再卡一期),"首启脚本先于其余类目"断言取消。设计文档 §1 D4 行与 §2 修订块是本条的完整论证。

---

## 0. 全局守则(每个 Phase、每个任务继承,不再重复)

### 0.1 运行与验证命令

测试跑在 `src/backend` 下,环境变量照 `scripts/ci_test.sh:96` 的口径:

```bash
cd src/backend
DEPLOY_PROFILE=test PYTHONPATH="src:." .venv/bin/python -m pytest tests/community/core/bot_config_manifest -v
# 单测定向:
DEPLOY_PROFILE=test PYTHONPATH="src:." .venv/bin/python -m pytest tests/community/core/bot_config_manifest/test_manifest_schema.py -v
# 全量 gate(推 PR 前,变行覆盖率以本地复现为准):
BACKEND_CI_SKIP_INSTALL=1 bash scripts/ci_test.sh
```

- 目标用例集中在 `tests/community/core/bot_config_manifest/`(镜像 `src/agentclaw/community/core/bot_config_manifest/`),路由层用例放 `tests/community/adapters/http/openapi_v1/bots/`(startup-script 现有用例同目录,照它们找文件)。
- 依赖安装:`uv sync --frozen`(ci 脚本同款);本地跳过用 `BACKEND_CI_SKIP_INSTALL=1`。

### 0.2 git/PR 规矩

- **分支**:`git checkout -b feat/bot-config-manifest-<slug>`(自 `origin/dev`);**与 dev 冲突一律 rebase,禁止 merge commit**。
- **推送**:触 apply 路径的 PR(W4/W5/W6/W8)push 带 `OCB_PRE_PUSH_RUN_CI=1`;其余项默认 pre-push 只跑 OCB gate(merge target `origin/dev`)。
- **提交信息**:`<type>(backend): <短结局>`;**不加任何 attribution/co-author 尾注**(用户全局规则已禁)。
- **PR**:标题同提交格式;正文 Problem / Solution / Validation(贴 `ci_test.sh` 摘要:用例数、行覆盖率、变行覆盖率)+ Spec 节指向该项 `src/backend/specs/<开工日>-<slug>/`;`Closes #<工作项 issue>`。
- squash 合并,标题即 commit message——标题里不要放 issue 号前缀以外的噪音。

### 0.3 公开面硬规矩(踩过的坑,违反=CI 挂或事故)

1. **每条新路由**:`.githooks` 的 principal seam 会让未登记 `ADMISSION` 的路由直接挂——在 `adapters/http/openapi_v1/admission.py:47` 的 `ADMISSION` dict 加一行(样例见该文件 :92-95 startup-script 三行),并补对应用例(找 `test_principal_seam.py` 现有用例格式)。
2. **`bots.openapi.json` 手工增量**——**禁止 any 全量 regen**(历史把手工维护内容冲过)。先 `git ls-files | grep -i openapi` 定位当前文件,新增 operation 条目手写进去。
3. **ocb 双仓同步**:gateway 路由配置(`~/IdeaProjects/ocb` 的 `application.yaml` 路由面)与本仓 `bots.openapi.json` 增量**同 PR 手工同步**——W1/W3/W4 三个 PR 都要。
4. **服务 API 协议**放 `community/api/`(`BotConfigManifestServiceProtocol` 等),**注册进** `tests/community/architecture/test_service_api_conformance.py:227` 的 `_PAIRS`(import 区 :60-130 加 import,`_PAIRS` 加二元组)。
5. core 永不 import `adapters`/路由层(架构测试强制,既有约定)。

### 0.4 每工作项的 SDD kickoff(仓库惯例,范本 `src/backend/specs/2026-08-10-bot-startup-script/`)

每个 Phase 第一步固定为:

```bash
mkdir -p src/backend/specs/<开工日>-<slug>/
# spec.md:该项目标+验收(从 issue 抄验收清单,不改写);plan.md:拆解;
# tasks.md:checkbox 任务清单(代码级步骤,本计划 Phase D–J 的细化落这里)
```

- 新模块首任务:`core/bot_config_manifest/README.md` 按 `docs/arch/context-boundary-format.md` 写 Context Boundary。
- 仓储协议/实现对:`core/repository/protocols/bot/<x>.py` + `core/repository/implementations/bot/<x>.py`。
- 每个 spec 目录随其 PR 合入(不是独立 PR)。

### 0.5 外部协调(单人执行期)

- **W10(#1509)**:开工当日(W1 PR 里)在 #1509 评论催收或确认排期;W4 之前再确认一次。
- **W12(#1684)**:范围修订后**不再卡 W8 一期**(一期 teclaw 只用既有逐文件通道);它卡的是契约文书面闭环与 W9 的 teclaw 臂——W9 开工时确认评审状态。
- 其余协作不动:teclaw 契约文档(`teclaw-cli-contract.zh-CN.md`,在未合并的 PR #1465 里)到 W9 再取用。

---

## Phase 0: 开工锚定(半小时,一次性)

- [ ] **S0.1** 跑通基线:`cd src/backend && BACKEND_CI_SKIP_INSTALL=1 bash scripts/ci_test.sh`,记录绿灯基线数(后续 PR 的 Validation 对比用)。若有红,先停下查环境,不带新债开工。
- [ ] **S0.2** 读三份先例文件(代码级开工要对着写):`core/repository/implementations/bot/startup_script.py`(仓储+`_script_key`)、`adapters/http/openapi_v1/bots/router.py:1315-1341`(路由样例)、`core/bot_management/token_vault.py`(W3 用)。
- [ ] **S0.3** `gh issue comment 1509 -R inclusionAI/Avernet -b "<催收 W10 时间表>"`(措辞自定)。

---

## Phase A: W1 — manifest 文档/schema/能力/API(#1469)

**Files(全部相对仓库根):**
- Create: `src/backend/src/agentclaw/community/core/bot_config_manifest/{__init__.py,README.md,manifest_schema.py,capabilities.py,feature_flags.py}`
- Create: `src/backend/src/agentclaw/community/core/bot_config_manifest/services/{__init__.py,manifest_service.py}`
- Create: `src/backend/src/agentclaw/community/core/repository/protocols/bot/config_manifest.py`、`.../implementations/bot/config_manifest.py`
- Create: `src/backend/src/agentclaw/community/core/bot_config_manifest/sql/<开工日>_bot_config_manifest.sql`
- Create: `src/backend/src/agentclaw/community/api/bot_config_manifest_service.py`
- Modify: `adapters/http/openapi_v1/bots/router.py`(追加 4 路由)、`adapters/http/openapi_v1/admission.py`(追加 4 行)、`.../openapi_v1/__init__.py`(如有路由聚合注册)、`tests/community/architecture/test_service_api_conformance.py`(_PAIRS)
- Modify: `bots.openapi.json`(手工增量,先 `git ls-files` 定位)
- Test: `tests/community/core/bot_config_manifest/{test_manifest_schema.py,test_capabilities.py}`、`tests/community/core/repository/.../test_config_manifest_repository.py`、`tests/community/adapters/http/openapi_v1/bots/test_config_manifest_api.py`
- Spec: `src/backend/specs/<开工日>-w1-manifest-document/`

- [ ] **A.0 kickoff**:建 spec 目录(Sch0.4 三个文件);`git checkout -b feat/bot-config-manifest-w1`;写模块 `README.md`。

- [ ] **A.1 DDL + 唯一键代理**(照 `core/bot_startup_script/sql/2026_08_10_bot_startup_script.sql` 同构,键推理注释照搬改写):

```sql
CREATE TABLE `ac_bot_config_manifest` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `env`           varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev',
  `entity_id`     varchar(1024) NOT NULL COMMENT '实体ID（bot 的 entity_id）',
  `bot_id`        varchar(256)  NOT NULL COMMENT 'Bot ID',
  `schema_version` int(11)      NOT NULL COMMENT 'manifest schema 版本（v1=1）',
  `document`      mediumtext    NOT NULL COMMENT '配置清单文档 JSON 原文（整份替换、不规范化、script 正文逐字节保真）',
  `size_bytes`    int(11)       NOT NULL COMMENT '文档字节数（UTF-8）',
  `modifier`      varchar(1024) NOT NULL COMMENT '审计：最后写入者',
  `avernet_tenant` varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '数据隔离租户',
  `manifest_key`  char(64)      NOT NULL COMMENT '唯一键代理：sha256(env|entity_id|bot_id)',
  `gmt_create`    datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_manifest_key` (`avernet_tenant`, `manifest_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 配置清单文档';
```

ORM `models.py`(放 `core/bot_config_manifest/models.py`,字段注释对照 `bot_startup_script/repository/models.py:90-101` 的写法)。注意 schema §5 限额:文档 ≤64 KiB → `document` 用 mediumtext 不变、`size_bytes` 校验在服务层。

- [ ] **A.2 失败测试(键代理+基础仓储)**:

```python
# tests/community/core/repository/.../test_config_manifest_repository.py
def test_manifest_key_is_stable_surrogate():
    k = _manifest_key("prod", "ent-1", "bot-9")
    assert k == hashlib.sha256("prod|ent-1|bot-9".encode()).hexdigest()
    assert _manifest_key("prod", "ent-1", "bot-8") != k  # bot 变则键变

def test_upsert_replaces_whole_document(repo):
    repo.save(env="dev", entity_id="e", bot_id="b", document='{"schema_version":1}',
              schema_version=1, modifier="u")
    repo.save(env="dev", entity_id="e", bot_id="b", document='{"schema_version":1,"script":{"body":"x"}}',
              schema_version=1, modifier="u")
    rec = repo.get(env="dev", entity_id="e", bot_id="b")
    assert rec.document == '{"schema_version":1,"script":{"body":"x"}}'  # 整份替换

def test_get_missing_returns_none(repo):
    assert repo.get(env="dev", entity_id="e", bot_id="none") is None
```

- [ ] **A.3 仓储实现**(分隔符、hexdigest 对照 `implementations/bot/startup_script.py:40` 的 `_script_key` 保持同构;读写都只按 `manifest_key` 过滤,不按三列):

```python
def _manifest_key(env: str, entity_id: str, bot_id: str) -> str:
    return hashlib.sha256(f"{env}|{entity_id}|{bot_id}".encode("utf-8")).hexdigest()
```

- [ ] **A.4 schema 模型与文档级校验**(`manifest_schema.py`)——pydantic 建单条模型(字段表=docs/…/manifest-schema.zh-CN.md §1-§3,逐类抄,不发明字段),文档级校验单独一个函数:

```python
@dataclass(frozen=True)
class Violation:
    entry: str   # 指名违规条目,如 "resources[2]" / "sources.content"
    rule: str    # 稳定规则码,如 "from-undeclared"
    message: str

def validate_document(doc: ManifestDocument) -> list[Violation]:
    ...  # 见下表,全部规则一次跑完;空列表=合法
```

必查规则(每条一个规则码,PUT 向 #1469 验收逐条对应):

| 规则码 | 检查 |
| --- | --- |
| `entry-multiple-source` | 一条目同时出现 `from`/`source`/`content` |
| `from-undeclared` | `from` 指向未在 `sources` 声明的名字 |
| `git-with-digest` | git 源上写了 `digest` |
| `auth-not-inline-source` | `from`/`content` 条目上写了 `auth` |
| `apply-once-reserved` | 任何位置出现 `apply_once` |
| `placeholder-unknown` | 未知 `${...}` 占位符(白名单 §4 表) |
| `resource-path-absolute`/`resource-dotdot` | 绝对路径或 `../` |
| `resource-nested` | `path` 嵌套在另一目录条目之下(跨条目:先收集目录条目 `path` 集合,再比对所有条目) |
| `limit-*` | 文档 ≤64KiB、每类 ≤50 条、内联 ≤64KiB(schema §5 写得进的部分) |
| `script-rejected-` … | 类目层能力:见 A.6 |

- [ ] **A.5 失败测试(校验矩阵,每个规则码至少一反例+一正例)**:

```python
def test_from_undeclared_is_named():
    v = validate_document(_doc(skills=[{"name": "q", "from": "nobody"}]))
    assert [(x.rule, x.entry) for x in v] == [("from-undeclared", "skills[0]")]

def test_nested_resource_path_rejected():
    v = validate_document(_doc(resources=[
        {"path": "data/kb/", "source": "https://x/kb.zip", "unpack": "zip"},
        {"path": "data/kb/inner.md", "source": "https://x/i.md"}]))
    assert (x.entry for x in v) 等价含 "resources[1]" 且 rule == "resource-nested"

def test_unknown_placeholder_rejected():
    v = validate_document(_doc(identity=[{"type": "SOUL.md",
        "source": "https://x/${TENANT}/soul.md"}]))
    assert any(x.rule == "placeholder-unknown" for x in v)   # ${TENANT} 不在白名单:${OCB_*} 才是

def test_script_body_roundtrip_exact():
    body = '#!/bin/bash\n echo "$(id)" && {token} \'quoted\''
    doc = _doc(script={"body": body})
    assert doc.script.body == body          # 引擎不清洗
```

- [ ] **A.6 capability 解析器**(`capabilities.py`——**单函数两入口**,GET、PUT、外部 W13 预检共用;矩阵=docs/…/engine-requirements.zh-CN.md §2):

```python
ARC_ENGINES = frozenset({"openclaw", "claude_code", "aicoding", "hermes", "moltis"})
PHASE1_APPLIED = frozenset({"mcp", "resources", "skills", "identity"})
# engine_config:第一期无物化器+T3 未闭 → 全引擎 false(fail closed,W13 起不得接受它)
# cli_tools:W9 落地前全引擎 false;W9 的 PR 把它翻开(ARCA 系 true,teclaw 视 T4)

@dataclass(frozen=True)
class CategorySupport:
    categories: dict[str, bool]
    reasons: dict[str, str]      # false 必带 reason

def supported_categories(engine_type: str, bot_type: str) -> CategorySupport:
    """(engine_type, bot_type) → 逐类目支持。纯函数、读表、不触库不触容器。
    未知引擎/形态 → 全 false(#935 教训:fail closed 不静默)。"""
```

**开工强校验步骤**(矩阵 7 行能否用二维表达的关键):读 `core/bot_startup_script/services/_support.py` 的 `resolve_support:152`,确认 LOCAL/singlebox/ARCA-direct/desktop 的判定输入是否在 `(engine_type, bot_type)` 二维内;若第三维(provider)才携带形态,则本函数签名不变,W1 的 GET 入口在 service 层先从 bot 记录解出形态再映射(映射表同样只在本文件),**函数本体永远纯**。把结论写进 spec 目录(这条影响 W13,必须留档)。

- [ ] **A.7 feature flags + 开关依赖**(照 `core/skill_center/feature_flags.py:27` 同形):

```python
# core/bot_config_manifest/feature_flags.py
@dataclass(frozen=True)
class BotConfigManifestFlags:
    api_enabled: bool = False      # 默认关;W5 落地(M3)后预发翻开

    @classmethod
    def from_env(cls) -> "BotConfigManifestFlags":
        return cls(api_enabled=os.environ.get("BCM_API_ENABLED", "").lower() == "1")

_FLAGS_SINGLETON: BotConfigManifestFlags | None = None

def get_bot_config_manifest_flags() -> BotConfigManifestFlags:
    global _FLAGS_SINGLETON
    if _FLAGS_SINGLETON is None:
        _FLAGS_SINGLETON = BotConfigManifestFlags.from_env()
    return _FLAGS_SINGLETON
```

路由层依赖(404 形态,错误类按 `openapi_v1/errors.py` 现有模式加 `ManifestDisabledError`,接 `@envelope_errors`):

```python
def _require_manifest_enabled() -> None:
    if not get_bot_config_manifest_flags().api_enabled:
        raise ManifestDisabledError()   # → 404
```

测试环境置 `BCM_API_ENABLED=1`(conftest fixture),并保留一条"关=404、开=200"的对照用例。

- [ ] **A.8 服务与协议**(`api/bot_config_manifest_service.py` 协议 + core 实现;注意 core 不 import 路由层):

核心方法(协议签名,实现照 startup-script service 形态):
- `get(env, entity_id, bot_id) -> ManifestDocument`(缺行 → **空文档**:`schema_version=1`、六类全空、无 script——never-fail)
- `put(env, entity_id, bot_id, engine_type, bot_type, document_json, modifier) -> PutResult`——all-or-nothing:`json 解析 → pydantic 模型 → validate_document → capability 过滤(engine_type, bot_type;script/identity 有引擎门,身份类别集按引擎校验 identity.type 合法集——照 `core/services/identity.py:60-81` 的 `VALID_IDENTITY_FILES`/`CLAUDE_CODE_IDENTITY_FILES` 对照)`,任一 Violation → 422+全部逐条返回,**不落库**;全过 → 整份替换入 `ac_bot_config_manifest`。`PutResult` 带未引用源的告警提示(`sources` 声明但无人 `from`,#1475 验收:是警告不是错误)。
- `delete(env, entity_id, bot_id)`——只删文档行,**不删任何实体**。

- [ ] **A.9 路由 4 条**(照 `bots/router.py:1315` 的 get_bot_startup_script 样式:同样 `Envelope[...]`/`responses=USER_SCOPED_403`/`dependencies=_GRANT_CHECKED_OWN_BOT`+追加 `_require_manifest_enabled`):

```python
@router.get("/{bot_id}/config-manifest", response_model=Envelope[...],
            responses=USER_SCOPED_403,
            dependencies=_GRANT_CHECKED_OWN_BOT + [_require_manifest_enabled])
@envelope_errors
async def get_bot_config_manifest(bot_id: BotIdPath, request: Request, owner_id: UserIdDep, ...):
    bot = bot_service.get_bot(bot_id, owner_id)      # 归属/租户守卫,样例 :1337
    doc = manifest_service.get(...)
    return envelope(_payload(bot_id, doc, capability), request)
```

四条:`GET/PUT/DELETE /{bot_id}/config-manifest` + `GET /{bot_id}/config-manifest/capabilities`(响应含逐类目支持与 reason,PUT 用的同一函数直接给)。**ADMISSION 四行**(GRANT_CHECKED_OWN_BOT,照 :92-95 模式):

```python
("GET", "/openapi/v1/bots/{bot_id}/config-manifest"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
("PUT", "/openapi/v1/bots/{bot_id}/config-manifest"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
("DELETE", "/openapi/v1/bots/{bot_id}/config-manifest"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
("GET", "/openapi/v1/bots/{bot_id}/config-manifest/capabilities"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
```

- [ ] **A.10 API 面与门禁**:`bots.openapi.json` 手工增量 4 个 operation(先定位文件、局部 diff);ocb 仓 `application.yaml` 路由面同步;`test_principal_seam.py` 用例;`_PAIRS` 注册 `(BotConfigManifestServiceProtocol, ManifestService)`。
- [ ] **A.11 必钉测试收尾**(除上述外):跨租户(两租户同 `bot_id`,A 写 B 读不到/盖不了——租户守卫,照 startup-script 测试);`PUT` 部分拒绝时**零写入**(重复 get 返回原文档);空 bot 第一次 GET=空文档 200;`script` 长度 ≤24KiB(`MAX_SCRIPT_BYTES` 同源常量,与 #935 相同口径)。
- [ ] **A.12 验证+交付**:`ci_test.sh` 绿 → `git add … && git commit -m "feat(backend): add bot config manifest document storage, schema v1 and capabilities API"` → `OCB_PRE_PUSH_RUN_CI=1 git push -u origin feat/bot-config-manifest-w1` → PR(body 规则 §0.2,`Closes #1469`)。**注意**:PR 合入后 `main`/dev 重跑一遍目标用例(远端 CI 与本地变行覆盖可能差异)。

---

## Phase B: W2 — 带防护的 fetcher 与解包(#1470)

**Files:** `core/bot_config_manifest/fetch/{__init__.py,limits.py,guarded_fetcher.py,unpack.py,credential_injection.py}`;`api/` 无新协议(W3 才绑定注入);Test: `tests/community/core/bot_config_manifest/fetch/{test_guarded_fetcher.py,test_unpack.py}`。

- [ ] **B.0 kickoff**:spec 目录 `<开工日>-w2-guarded-fetch/`;**移植参照**读一遍 `src/engine/src/engine/community/plugins/resource_materialization.py`(结构/阈值思路:URL 形状→`is_global`→连接期 pinning+SNI→双重大小上限)——**不 import,抄结构**。
- [ ] **B.1 限额常量**(`fetch/limits.py`,单一来源,后续 W6/W9/W11 直接引用,取 schema §5):

```python
FETCH_ENTRY_LIMITS = MappingProxyType({
    "skills": 100 << 20, "resources_file": 100 << 20,
    "identity": 1 << 20, "cli_tools": 200 << 20,
    "resources_archive": 200 << 20, "resources_unpacked": 500 << 20,
})
ARCHIVE_MEMBER_LIMIT = 5000          # 单归档文件数
APPLY_FETCH_TOTAL = 500 << 20        # 单次 apply 总预算
FETCH_TIMEOUT_S = 60.0
APPLY_BUDGET_S = 300.0
MAX_REDIRECTS = 5
```

- [ ] **B.2 失败测试(安全矩阵,先写)**——用注入 resolver 的假 DNS + `httpx.MockTransport`/本地 aitestserver,**不依赖外网**:

```python
def test_private_ip_rejected(cookie):
    for host in ["127.0.0.1", "10.0.0.9", "169.254.169.254", "fd00::1",
                  "192.168.1.1", "224.0.0.1", "240.0.0.1"]:
        with pytest.raises(FetchRefusedError, match="non-public"):
            _fetch(url=f"https://{host}/x")

def test_rebind_cannot_reach_private():     # 检查期公、连接期私 → 必须拒
    resolver = RebindResolver(public_first=["203.0.113.9"], private_after=["127.0.0.1"])
    with pytest.raises(FetchRefusedError):
        _fetch(url="https://rebind.attacker/x", resolver=resolver)

def test_redirect_leaving_allowed_prefixes_fails(cred, cookie):
    with pytest.raises(FetchRefusedError, match="redirect"):
        _fetch(url="https://ok.example/data", auth="cdn", policy=prefix_policy, ...)

def test_streamed_size_cap_enforced_during_read(mock_short_content_length):
    with pytest.raises(FetchRefusedError, match="exceeds"):
        _fetch(url="https://ok.example/big", limits=...)   # content-length 谎报小,流式累计超限

def test_digest_mismatch_is_failure_not_success(...):
    with pytest.raises(FetchError, match="digest"):
        _fetch(url=..., expected_digest="sha256:" + "0" * 64)
    # "损坏的成功"=验收反面:绝不返回 bytes
```

- [ ] **B.3 guarded_fetcher 核心实现**(骨架实写;httpx 已是社区 core 既有依赖):

```python
def _validate_url(url: str) -> httpx.URL:
    u = httpx.URL(url)
    if u.scheme != "https" or not u.host:          # http 仅部署级白名单,见 _http_allowed
        raise FetchRefusedError(f"untrusted scheme/host: {safe_url(u)}")
    if u.userinfo:
        raise FetchRefusedError("userinfo in URL")
    return u

def _resolve_public_ips(host: str, resolver: Resolver | None) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addrs = sorted({sa.sockaddr[0] for sa in (resolver or _default_resolver()).getaddrinfo(host, 443)})
    ips = [ipaddress.ip_address(a) for a in addrs]
    if not ips or not all(ip.is_global for ip in ips):
        raise FetchRefusedError(f"non-public address for {safe_host(host)}")
    return ips

def fetch(request: FetchRequest) -> FetchedObject:
    url = _validate_url(request.url)
    redirect_budget = MAX_REDIRECTS
    current = url
    while True:
        ips = _resolve_public_ips(current.host, request.resolver)
        ip = min(ips)                       # 确定值→连接与校验之间无窗口
        headers = request.injector.headers_for(current) if request.injector else {}
        with httpx.Client(follow_redirects=False, timeout=FETCH_TIMEOUT_S) as client:
            resp = client.get(str(current.copy_with(host=str(ip))),
                              headers={**headers, "host": current.host},
                              extensions={"sni_hostname": current.host})
        if 300 <= resp.status_code < 400:
            current = _validate_redirect(resp, request.policy, request.policy_ctx)   # 每跳重校验+前缀判定
            redirect_budget -= 1
            if redirect_budget < 0:
                raise FetchRefusedError("too many redirects")
            continue
        resp.raise_for_status()
        return _collect_streamed(resp, request.limits, request.expected_digest)
        # _collect_streamed:分块累计字节(超限即抛,不等 body 完)、sha256 单次遍历同时算、
        # digest 不匹配 raise FetchError——绝不"成功返回坏字节"
```

`credential_injection.py` 只放 Protocol:

```python
class CredentialInjector(Protocol):
    def headers_for(self, url: httpx.URL) -> dict[str, str]: ...
class AuthorizationPolicy(Protocol):
    def reauthorize(self, url: httpx.URL) -> None: ...    # 越界 raise(由 W3 绑定前缀判定)
```

- [ ] **B.4 解包**(`unpack.py`;Python 3.12 tarfile 用 `filter="data"` 兜底 + 自有显式检查,zip 手工):

```python
def unpack_archive(data: bytes, kind: Literal["zip", "tar.gz"],
                   strip_components: int, limits: ArchiveLimits) -> UnpackedTree:
    # zip: zf.namelist() 先全量过检(绝对路径/../、成员数、累计未压缩大小)再逐个 z.extract 后 chmod
    # tar.gz: tf.getmembers() 先过检(mode 位、symlink 目标逃逸、设备成员)再 tf.extractall(filter="data")
    # 两者最后统一:os.chmod(path, mode & 0o644)  → 权限抹平"任何东西不可执行"
    # strip_components:恰好剥 N 段,绝不自动探测单一顶层目录
```

测试表驱动:zip-slip 每变体、tar 绝对路径成员、symlink 逃出根、设备成员(`char/block/fifo`)、成员数超限、解包总大小超限、mode 抹平、`strip_components` 边界(壳目录存在/不存在行为一致)。

- [ ] **B.5 PR**:标题 `feat(backend): add guarded fetch and unpack pipeline for manifest sources`。此 PR 落地后跑一轮 security-reviewer 复审(它是全特性安全底座)。`Closes #1470`。

---

## Phase C: W3 — 租户级源凭证(#1471)

**Files:** `core/bot_config_manifest/services/source_credential_service.py`、`core/repository/{protocols,implementations}/bot/source_credential.py`、`adapters/http/openapi_v1/source_credentials/{__init__.py,router.py}`;SQL:`ac_source_credential`。

- [ ] **C.1 DDL**(加密列直接存 `TokenVault.encrypt` 产物,可 `enc:v1:` 可(残留异常)明文——参照 token_vault 的零迁移口径):

```sql
CREATE TABLE `ac_source_credential` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw' COMMENT '租户',
  `name` varchar(128) NOT NULL COMMENT '凭证名（自由标识符）',
  `type` varchar(32) NOT NULL COMMENT '认证机制:v1 仅 header;oss_aksk/basic 预留',
  `header_name` varchar(128) NOT NULL COMMENT '注入的请求头名',
  `secret` text NOT NULL COMMENT 'enc:v1:<密文>（译码仅 fetch 前内存中）',
  `allowed_prefixes` text NOT NULL COMMENT 'JSON 数组:绝对 https 前缀',
  `modifiers` ... — 与 startup-script 同款审计/时间列,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_name` (`avernet_tenant`, `name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='源凭证（租户级）';
```

(写代码时把审计/时间列补全,与 A.1 同款,不省略。)

- [ ] **C.2 前缀匹配——先写失败测试**(负例就是 issue 的原例):

```python
@pytest.mark.parametrize("target,prefix,want", [
    ("https://h/x/team/content",    "https://h/x/team/content", True),
    ("https://h/x/team/content/70", "https://h/x/team/content", True),
    ("https://h/x/team/content-secret", "https://h/x/team/content", False),  # 段边界
    ("https://h/", "https://h/", True),        # 整 origin 必须显式
    ("https://h/anything", "https://h/", True),
    ("https://other/x", "https://h/x", False),
    ("http://h/x", "https://h/x", False),      # scheme 也必须钉死
])
def test_prefix_segment_boundary(target, prefix, want):
    assert url_matches_prefix(target, prefix) is want
```

实现(路径段边界,写完整版):

```python
def _norm_path(path: str) -> str:
    path = path or "/"
    return path if path.startswith("/") else "/" + path

def url_matches_prefix(target: str, prefix: str) -> bool:
    """目标等于前缀、或以「前缀 + '/'」开头;整 origin 必须显式写 https://host/。"""
    t, p = urlparse(target), urlparse(prefix)
    if t.scheme != "https" or p.scheme != "https":
        return False
    if t.netloc != p.netloc:                       # host[:port] 精确比较
        return False
    if t.username or t.password or p.username or p.password:
        return False
    tp, pp = _norm_path(t.path), _norm_path(p.path)
    if pp == "/":                                  # 显示声明的整 origin
        return True
    if tp == pp:
        return True
    return tp.startswith(pp if pp.endswith("/") else pp + "/")
```

- [ ] **C.3 fail-closed 守卫测试先行**:

```python
def test_prod_profile_without_master_key_refuses_write():
    svc = _svc(vault=TokenVault(""), fail_closed=True)
    with pytest.raises(MasterKeyUnavailableError):
        svc.put_credential(name="corp", body=_header_body())

def test_singlebox_empty_master_key_writes_plain(vault=TokenVault("")):
    svc = _svc(vault=TokenVault(""), fail_closed=False)
    svc.put_credential(name="corp", body=_header_body())  # 明文落库,passthrough 既有行为
```

服务构造注入 `fail_closed`(`di/modules` 生产 profile 传 True、test/singlebox 传 False——沿 `di/profile_modules.py:140` 的 profile 分流形态,不新发明 profile 判断)。

- [ ] **C.4 掩码读+预留类型拒绝测试**:`GET` 永不回 `secret`(响应只有 `has_secret/header_name/allowed_prefixes/updated_at`);
`type in {"oss_aksk","basic"}` PUT → 409。`auth` 引用不存在名:PUT manifest 告警(#1471 校验条)在本项 CIT 提示、apply 报 failed 在 W4/W5 联动——本项只落"引用不存在"的查询接口给 W1 用。
- [ ] **C.5 路由 4 条+ADMISSION+OpenAPI 增量+ocb 同步**(§0.3 全套;租户级路径无 bot 前缀,权限模式**照 mcp 统一配置路由**的现况取一致——写代码前先看 `openapi_v1/mcp/router.py` 的 dependencies 与对应 ADMISSION 行,抄同款)。
- [ ] **C.6 认证失败具名化**:`fetcher` 捕 401/403 → `CredentialRejectedError(name)`(W4 报告层区分用),本项测试断言错误消息含凭证**名**不含值。
- [ ] **C.7 PR**:`feat(backend): add tenant-level source credentials with prefix authorization`,`Closes #1471`。

---

## Phase D: W11 — 平台物化与留存(#1510)

依赖:W2 的 `FetchedObject` 形状(已合)。

- [ ] **D.0 kickoff**:spec 目录;**先读** `_collect_streamed` 实际产出类型,据此定 `ContentStore.put(FetchedObject) -> StoredObject`。
- [ ] **D.1 DDL** `ac_manifest_object`:`content_sha256 char(64)` uk(带 tenant 前列:uk `(avernet_tenant, content_sha256)`)、`bytes longblob`、`size_bytes`、`source_url varchar(1024)`、`resolved_sha varchar(64) NULL`(git)、`fetched_at`、`credential_name varchar(128) NULL`(**只存名**)、`category varchar(32)`。
- [ ] **D.2 仓储+服务**:`resolve(content_sha)` 命中返回整对象(溯源**覆盖更新**——同哈希再拉时刷新 source_url/ref/fetched_at/credential_name);`keep_last`/下发/审计一律走它。
- [ ] **D.3 必钉测试**:
  - 同字节两次拉取只存一行(uk)+溯源字段被第二次刷新;
  - **重试不重新拉取**:拉取成功后被注入的 fetcher 拒绝服务,重试 apply 条目仍 `unchanged`、fetcher 调用计数 = 1;
  - 凭证值绝不入表、入日志(扫描断言);
  - 大小双卡(W2 上限+schema §5 限额同一常量,复用 `fetch/limits.py`,不第二处定义)。
- [ ] **D.4 README 留存陈述**(审计对齐的"显式陈述"):内容寻址+被引即留,无时间窗清理,`keep_last=1` 是条目级语义非全表轮换;v2 评审再上清理。写在 `core/bot_config_manifest/README.md` 的边界块里。
- [ ] **D.5 PR**:`feat(backend): add content-addressed manifest object store and retention`,`Closes #1510`。

---

## Phase E: W4 — apply 引擎/记录/免取源物化器(#1472)

依赖:W1(文档+能力)、**W10 缝(#1509——开工先确认已合;未合则按设计 §4.4 只做 `ManifestGuards` Protocol+fake,合后补接线 PR,绝不自写第二份鉴权)**。此 PR 起 push 带 `OCB_PRE_PUSH_RUN_CI=1`。

- [ ] **E.0 kickoff**:spec 目录;读 `BotRestartLockRepository`(`protocols/bot/bot.py:386`/`implementations/bot/restart_lock.py:49`)与 `bot_publish_service.py:1285-1293`(锁+租户线程模式)。
- [ ] **E.1 两个物化器(免取源先行,TDD)**:
  - `script_materializer`:A 阶段直调 `BotStartupScriptService.put`(`bot_startup_script/services/startup_script_service.py:102`);`resolve_support:152` 既有判定复用——不支持引擎类目级 `failed` 带 reason;
  - `mcp_materializer`:`server_code` 注册表引用→既有启用+配置服务(开工锚定:`core/mcp/config_flow.py:83` 一族+`check_mcp_permission` 所在处;读 openapi_v1/mcp 或 skills router 的调用姿势,复用 service,不重写 HTTP 层逻辑);**MCP 凭证照旧由平台持有,manifest 零接触**。
- [ ] **E.2 编排器形状**(骨架约束,`apply(phases=...)` 可整可段=该形状唯一验收):

```python
class ApplyService:  # 协议进 api/,实现 core
    def apply(self, env, entity_id, bot_id, *, trigger: ApplyTrigger,
              phases: Sequence[Phase] = ("A", "B"), dry_run: bool = False) -> ApplyReport
# A=[script];B=[identity,resources,skills,mcp] 固定顺序;同 bot 串行:
# ac_manifest_apply_lock(照 restart_lock 的 acquire/release+lock_token fencing 另写,不改既有表)
# dry_run:全 plan 化,任何仓储/服务写调用为 0(含报告表)
```

**范围修订落点**:「可整可段」形状**保留**(二期 create 流挂接靠它,issue 原话"否则 W13 只能绕开它");但**一期没有任何调用方在创建路径触发 A 阶段**——显式 apply 内 A 先 B 后一次跑完,script 当次写库/下次启动生效(#935 口径)。**不要**在一期给"A 必须先于 `_build_create_bot_payload`"写任何测试或接线,那是二期验收项;A.「byte-identical」由 W8 的既有断言独立保障。

- [ ] **E.3 覆盖语义物化到 `Materializer` 接口**(接口落 `core/bot_config_manifest/protocols.py`,与设计 §3.1 对齐):`ApplyMaterializer.apply_category(declared) -> CategoryResult`,类目 all-or-nothing、逐条 `created/updated/unchanged/skipped/failed`、`on_fetch_failure ∈ {keep_last,fail}`(`skip` 已废,PUT 拒绝=W1 已落)。`engine_config`/`cli_tools` 两类目 capability=false、v1 无物化器,PUT 拒绝(W9 落地翻 `cli_tools` 位)。**保留名**:`MEMORY.md`/`IDENTITY.md` 在 identity 物化器里显式跳过——它们本来就在 `VALID_IDENTITY_FILES` 白名单里,所以用叠加的 `RESERVED_IDENTITY_FILES` 常量挡住,**不要从公共白名单里删**(那是 W13/其他读者共用的词汇表)。
- [ ] **E.4 报告存储+路由**:`ac_manifest_apply`(头+entries JSON 列;键同 A.1 推理:`apply_key = sha256(env|entity_id|bot_id)`、uk `(avernet_tenant, apply_key)`,**只存最近一次**:同键行 upsert);`POST .../config-manifest/apply`、`GET .../config-manifest/last-apply` 两路由+ADMISSION+OpenAPI 增量+ocb 同步+principal seam 用例+`_PAIRS` 注册 `ApplyServiceProtocol` 对(E.2 已声明协议进 api/)。
- [ ] **E.5 必钉测试**(全计划最重要的一组,一个都不可少):
  - 声明 `{A,B}`、B 物化失败 → B 原内容**零损伤**(B 类目仓储写调用=0)、A 正常、报告逐条指名;
  - 二次 apply 全 `unchanged` 且**全仓储写调用计数=0**;
  - `skills: []` → 删空(含"界面装的"同源记录);`DELETE manifest` → 什么都不删;**同一测试文件里两行为对照**(issue 点名);
  - `MEMORY.md/IDENTITY.md` 永不写入(声明 `type: MEMORY.md` 也 skip,报告说理由);
  - dry_run:写调用=0 **包括报告表**;
  - 租户错位:apply 在租户 A 上下文对租户 B bot → 拒(隔离故障,不是普通错误);
  - 锁:并发两个 apply,后到的等待/拒,无交织写。
- [ ] **E.6 PR**:`feat(backend): add two-phase manifest apply engine with records and fetch-free materializers`,`Closes #1472`。**分支保护**:此 PR 触 apply 路径,push 必带 `OCB_PRE_PUSH_RUN_CI=1`。

---

## Phase F: W5 — skills/identity 物化(#1473)

依赖:W2/W3/W4 全合。**M3 里程碑:合入后翻 `BCM_API_ENABLED`(预发)**。push 带 CI 环境变量。

- [ ] **F.0 kickoff**:spec;读 `core/skill_center/services/local_skill_upload_service.py:68`+`direct_activation_service.py:59`(上传+激活的真实签名);读 `core/services/identity.py:400-425`(`write_identity_file` 的引擎解析/校验)。
- [ ] **F.1 TDD 新物化器**:
  - skills:**必须**经 LocalSkillUploadService+DirectActivationService(目录形态直通、zip 形态分派);非 git 源无 `digest` PUT 拒(W1 已校验,这里断言防线未绕过:fetch 前 digest 缺失硬失败);
  - identity:`write_identity_file` 复用,`type` 写入时校验(校验本身就在 IdentityService 里——测试断言 manifest 路径对 claude_code bot 写 SOUL.md 被 `failed` 而非静默跳过);
  - `${OCB_*}` 替换:fetch 前+前缀判定前;负例:`${OCB_BOT_ID}` 展开后突破 `allowed_prefixes` → 条目 failed(不是照常拉)。
- [ ] **F.2 必钉测试**(issue 原条):
  - manifest 装的 skill 与手工上传同 zip **无法区分**(同一上传服务入参等价断言)+ 在 `SkillsPoolReconcileService.reconcile`(`reconcile_service.py:86`)一轮后**存活**;
  - quarantine cleanup 不清 managed 实体;
  - 单条拉取失败:只中止本类目、其余类目照走、bot 照常启动;
  - 内联 `content` 带 `auth/digest` → PUT 422(W1 联动回归)。
- [ ] **F.3 PR**:`feat(backend): materialize manifest skills and identity from pinned URL sources`,`Closes #1473`;预发开闸一次(配置见 §3.4 入口)+人工冒烟(GET/PUT/apply/last-apply 全链)。

---

## Phase G: W6 — resources 文件/目录(#1474)

依赖:W5。push 带 CI 环境变量。

- [ ] **G.0 kickoff**(⚠ 设计文档点名的**开工首锚**):定位"既有资源服务"——`rg "workspace" src/backend/src/agentclaw/community/core | rg -i "resourc"` 起步+读 schema §3.2 表格第三行指向的 `openapi_v1/resources` 同源服务路径;两个候选的决策规则照设计 §4.7(优先既有服务,否则经 `DeviceFileSystem` 组合仍是 core 服务)。**锚定结论写回设计文档 §4.7 与本项 spec**。
- [ ] **G.1 TDD 目录所有权**:整归档收敛(内容哈希同 W11 复用→`unchanged` 零写);**替换原子性**(temp+rename,失败不留半树);树内手工文件清/树外文件存活双向;嵌套禁令 PUT+apply 双层(W1 复查);限额三卡(§5:单归档/解包总/成员数——常量 `fetch/limits.py`);
- [ ] **G.2 teclaw 平铺**:`FileRef`/`ResourceRef` 逐文件进 `BotConfigArtifact`(`kernel/bot_config/artifact.py:97/175`),v1 不做子树优化(T5 出结论前)。
- [ ] **G.3 PR**:`feat(backend): add manifest resources with atomic directory ownership`,`Closes #1474`。**若砍单到 W6**:整 phase 移出 v1,同时解掉 D5 的串行依赖(设计 §2 预案),spec 留档不留半成品代码。

---

## Phase H: W8 — 生命周期 apply 点(#1476)

依赖:W4+W5+W6 合入。push 带 CI 环境变量。**范围修订后**(2026-08-31,计划头部修订块):一期**只挂 bot 已存在的生命周期事件**——`create_flow`/Passport(外部 W13)与 `TeclawProvisionService` 创建序**不在一期范围**;issue 验收里「teclaw 第一份 artifact 已含 manifest 结果」随启动前下发推二期(#1508),**W12 不再卡本项**。排期留半天吸收 publish 回归。

- [ ] **H.0 kickoff**:spec 目录;读 `bot_publish_service.py` 的 publish/republish 流与 `_do_restart`(约 :1285,含 `bind_current_avernet_tenant` 线程包装 :1291)、`core/devices/services/{device_sync.py:36,teclaw_device_sync.py:143,baas_device_sync.py:50}`。**开工先在 #1476 comment 范围修订**(说明:一期启动后下发、teclaw 首份 artifact 推二期 #1508),避免验收口径对不上。
- [ ] **H.1 生效通道**(issue 验收逐条落,BaaS 与 teclaw 同 PR 或分臂均可):
  - **PUT 立即生效不重启**(BaaS/ARCA):identity/resources 走 `DeviceFileSystem` 写、skills 走全量 symlink 收敛经 `DeviceSyncDispatcher`/`sync_symlinks` 带字段"下次启动生效";
  - **teclaw 臂**:ACTIVE 后逐文件写(`TeclawDeviceFileSystem`——第一期 teclaw 唯一生效路径),`sync_symlinks([])` 只给确认需要的类目,不默认拿;
  - **apply 点接线**:republish 与重建式 restart 处调用 `ApplyService`(trigger=republish/restart;租户上下文按 `_do_restart` 同款线程包装模式传递——**在构造点包装,绝不当装饰器用**);
  - **扩容不 re-apply**(做接线时顺手断言,不专门做)。
- [ ] **H.2 必钉测试**:
  - 路径守卫:挂接代码路径上**不出现** `BotService.restart_bot` 调用(`bot_service.py:4291` 会抛 `BotOperationNotAllowedError`,但守卫断言的是"根本没人调",用调用关系断言而非异常断言);**同款守卫断言 `TeclawProvisionService` 一期零改动**(import/调用图不新增对它的引用);
  - 扩容零 apply:scale-out 路径 apply 调用计数=0;
  - 无 script bot 启动命令 **byte-identical**(#935 既有断言直接保留,别动它——一期唯一的启动命令不变式);
  - ~~script 先于其余类目~~(范围修订后**取消**——一期无启动前下发,断言无对照对象;二期做启动前下发时随 #1508 重建);
  - apply 报告**不写 bot 记录**(bot 行 update 计数=0);
  - §2.7:显式 apply 与 republish 触发同报告形态,trigger 字段区分。
- [ ] **H.3 PR**:`feat(backend): wire manifest apply into publish and rebuild lifecycle points`,`Closes #1476`——PR body 引用 H.0 的范围修订 comment;若仍有未在本期落地的 issue 验收条,逐条列明"二期 #1508",不遗留模糊。

---

## Phase I: W7 — 命名源+git 源(#1475)

- [ ] **I.0 kickoff**:spec;**30 分钟 spike**:先用 `uv add --dev dulwich` 验证浅 fetch 能力(depth=1 单 ref + HTTP Basic 凭证注入),验收通过再转正式依赖。dulwich 是本地实现,fetch 天然不执行 server hooks/filter(这与 W7"只读"验收直接对应)。不达验收→备选 smart-HTTP packfile 手拉,spike 结论入 spec。
- [ ] **I.1 落点(设计 §4.9)**:`fetch/git_source.py`(浅 fetch+`{git,ref}`→SHA+树缓存,每 apply 只拉一次);`materializers/named_source_resolver.py`(`sources`/`from` 互斥+引用图消化——W1 校验已静态拒绝非法引用图,此处运行期确认);凭证走 W3 注入(git 变体 HTTP Basic)。
- [ ] **I.2 必钉测试**:
  - **原子升级**:多条目引用同一命名源,改 `ref` 一次 apply 全条目同 SHA(报告 sources 段 `ref+resolved_sha` 双记);
  - 同一 `{git,ref}` 单次 apply **只拉一次**(fetcher 计数=1);
  - tag 重指下次 apply 收敛到新内容;
  - git 目录条目免 `unpack`/`strip_components`(不带 `unpack` 的目录条目在 URL 源被拒——三层规则互锁断言);
  - git 源写 `digest` → PUT 拒(W1 回归)。
- [ ] **I.3 PR**:`feat(backend): add named and git sources with atomic ref upgrades`,`Closes #1475`。

---

## Phase J: W9 — cli_tools(#1477)

(第一砍单台阶;启动前看排期余量。)

- [ ] **J.0 kickoff**:spec;读 `src/backend/docs/bot-config-manifest/teclaw-cli-contract.zh-CN.md`(已在 dev,commit 898ad7ef6),发 teclaw owner 评审(剩余日历成本在对方);**定位** singlebox 手工放置 `bcs-cli` 的脚本(`rg -l "bcs-cli" scripts/ src/ --glob '!*.md'`,A2 先例提到 `scripts/modules/bots.sh` 起步)。
- [ ] **J.1 落点(设计 §4.10)**:
  - `materializers/cli_tools_materializer.py`:digest 强制(W1 校验)+ELF 只读校验(`EI_MAG`/`e_machine==EM_X86_64`/有 `PT_INTERP` 即拒,自写 48 行实现,不引 pyelftools);
  - 逻辑工具目录(NAS)+ per-env PATH 注入(沿 A2:先确认各引擎 gateway 环境注入点,backend 单方面先落 **openclaw** 注入=bcs-cli 的现有形态产品化),**物理路径不透给用户**;
  - 默认技能集工具-用法 skill:`SkillSetService.ensure_default_skill_set:1106`(engine_type 维度现成);
  - 替换 singlebox bcs-cli 手工放置(首个消费者——改脚本为消费 manifest,双轨期一档)。
- [ ] **J.2 必钉测试**:digest 缺失 PUT 拒;错误 arch 二进制(digest 正确!)→ apply `failed` 带独立的 "ELF" 原因;`PT_INTERP` 动态二进制拒;工具经 DEFAULT 技能集可见(`SkillSetService` 单测);PATH 注入点回归(openclaw gateway 环境构造断言)。
- [ ] **J.3 PR**:`feat(backend): add manifest cli_tools with ELF validation and PATH injection`,`Closes #1477`。

---

## 收尾(全序列完成后一次过)

- [ ] **R.1** 全量 `ci_test.sh`(含变行覆盖)绿;`BCM_API_ENABLED=1` 全链端到端(创建→PUT→apply→last-apply→PUT 立即生效)手工一遍(经 #1696 若已合)。
- [ ] **R.2** 设计文档回写:W6 锚定结论、W7 工具选型结论、W12/W10 实际闭环时间表——把 §8 风险表销项。
- [ ] **R.3** 10 个 issue 全部确认关闭;#1510 work-items 关联的策划 PR #1465 review 状态跟进(它含 contract 文档,W9 曾依赖)。
- [ ] **R.4** 记忆更新:本计划执行过程中实际踩到的坑(行号漂移/DI 注入差异/admission 新 mode 需求等)更新到 `avernet-openapi-surface-facts` 或新记忆文件——**开工时的新事实,不是计划期猜测**。

## 砍单预案(见设计 §2,不变)

进度告警时按序整 Phase 后移=W9→W7→W6;每一级砍单后重估 D5 串行与 PR 列表,W6 被砍时 H.0 前置依赖减项(设计 §2 已论证)。W1/W4/W5/W8+外部 W13 四件不可砍。
