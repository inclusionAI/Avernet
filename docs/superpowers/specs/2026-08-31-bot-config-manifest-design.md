# Bot Config Manifest — 单人实现设计与执行计划（2026-08-31）

> **状态：DRAFT，待用户评审。**本文把 bot-config-manifest 的 10 个工作项转成可执行的工程设计与交付序列。
>
> - **上游设计（定稿，已合入 dev）：**`src/backend/docs/bot-config-manifest/{README,design,manifest-schema,examples,engine-requirements}.zh-CN.md`
> - **工作拆分（已合入 dev，commit 898ad7ef6）：**`work-items.zh-CN.md` W1–W13 全景、逐项验收标准、砍单顺位
> - **本文范围：**W1(#1469) · W2(#1470) · W3(#1471) · W4(#1472) · W5(#1473) · W6(#1474) · W7(#1475) · W8(#1476) · W9(#1477) · W11(#1510)
> - **范围假设（需确认）：**这 10 项由**一人连续交付**。W10(#1509 服务缝)、W12(#1684 teclaw 语义契约)、W13(#1696 manifest 创建 bot)不在交付范围内，按外部依赖处理。
> - 上游设计文档定义"是什么/为什么"；本文定义"怎么落"——模块、表、接口、锚点、测试、序列。验收标准以 issue 为准，本文不复制，只做工程化决策。

## 1. 已锁定决策（不重开，全文引用编号）

| 决策 | 内容 | 来源 |
| --- | --- | --- |
| D1 | manifest 存储：bot 级一行，键与 `ac_bot_startup_script` 同推理（`(avernet_tenant, manifest_key)`，`manifest_key = sha256(env, entity_id, bot_id)`） | #1466 已解决 |
| D2 | 覆盖语义=**类目覆盖**：声明类目 apply 后等于声明；未声明类目不碰；`[]` = 删除全部 | #1467 修订 |
| D3 | apply（W4）不是 W11 的硬依赖——三方 diff 已撤销 | work-items §6 |
| D4 | **一期全局策略：所有引擎系、所有 apply 点，下发都在 bot 启动/激活之后**（用户 2026-08-31 拍板，取代原「BaaS 系临时策略、teclaw 首份 artifact 含 manifest 结果」的混合形态）。启动前下发的三件事——teclaw 首份 artifact 含 manifest、创建流程内 apply、W4-A 阶段挂 `_build_create_bot_payload`——**整体推第二期，#1508 跟踪**。代价照 §3.4 直说：首启存在「已 ACTIVE 但未配置」窗口（若外部 W13 一期形态不在，无人观察这个窗口）；换来的是第 4 天排期风险解除 | #1508、work-items §3.4 |
| X1 | git 源=浅层单 ref fetch + 机器账号 `read_repository` token（HTTP Basic）；**不走归档 API**，设计 §10.5 被取代 | W7 issue |
| X2 | teclaw artifact 重投=全量覆盖（owner 已确认 2026-08-30）；与 D2 同一操作 | W12 issue |
| X3 | ARCA 机群=linux/amd64，`cli_tools` 每工具一个 URL | W9 issue |
| X4 | desktop 不在范围内，能力表完全确定 | #1469 |
| 两条铁律 | ① apply 只经既有 core 服务写实体（design §2.3 硬规则，绕过=评审阻断项）② `_get_start_cmd` 无 script 时 byte-identical（#935 既有断言） | design §10 |

## 2. 交付序列（单人重排）与里程碑

原双人 7 天计划的关键链 `W1→W4→W5→W6→W8` 不变；交付交接（W4→W5、W6→W8）因单人执行消失。6.0 人日是**预算**（原估约 24 人日的 3.4 倍压缩），单人按 8–10 个工作日排，预留评审返工。

```text
D1─2   W1(0.75)──►W2(0.75)──►W3(0.75)──►W11(0.5)     全部无依赖,W4 前置攒齐
                      │
D3─4   W1 ✅ ──────► W4(0.75) ⚠ 等W10缝 ─► W5(1.0)
D5     W6(0.75) ──► W8(0.25)     W12 不再卡一期(范围修订)
D6+    W7(0.25) ──► W9(0.25)                          均为砍单台阶
```

| 里程碑 | 内容 | 验收口 |
| --- | --- | --- |
| M1 | 接入面完备：schema 校验、能力表、凭证面（W1+W3，路由在开关后） | 每条路由有 ADMISSION+用例 |
| M2 | 显式 apply 跑通 `mcp`/`script` 两类免取源物化器（W2/W3/W11/W4） | `POST …/apply` + `GET …/last-apply` + dry_run |
| M3 | **业务场景端到端**：`skills`+`identity` 从 URL 源装上（W5）→ 开特性开关 | manifest skill 与手工上传不可区分 |
| M4 | `resources` + 生命周期挂接（W6+W8 全臂：PUT 生效+republish/重建点；一期全为启动后下发） | PUT 立即生效不重启+扩容零 apply |
| M5 | git 源 + cli_tools（W7+W9，按砍单顺位保底） | {git,ref} 原子升级 / ELF 校验 |

**砍单顺位（7 天顶不住时，原判不变）**：W9 → W7 → W6。W1/W4/W5/W8 及（外部的）W13 不可砍。砍 W6 同时解掉 D5 的 W6→W8 串行。

**外部卡点与应对**：
- **W10（#1509，totalfrank，0.25d，D1）**：W4 硬依赖。开工日即催；若 D3 仍未落地，W4 先落编排器与两阶段骨架、物化器与报告，缝的接线上留单点（见 §4.4），W10 合并后补一个接线 PR。**不得为绕它另写第二份校验/鉴权**（那是 W4 的验收反面）。
- **W12（#1684）**：语义确认（artifact 重投=全量覆盖）已闭环，剩余日历成本是 teclaw 对 `cli_tools` 增量的评审。**范围修订后 W12 不再卡 W8 的一期交付**——一期 teclaw 臂只剩 ACTIVE 后逐文件通道（现状机制），覆盖语义由平台侧物化器（类目 all-or-nothing）保证；W12 只卡契约文书面闭环与 W9 的 teclaw 臂。
- **W13（#1696，totalfrank）**：反向依赖我们的 W1（能力两入口）、W4（两阶段编排）、W5（物化器全集）。**范围修订后它的「第一份容器即带配置」承诺随启动前下发放到二期（#1508）**——一期它若交付，可用形态退化成「创建后 PUT manifest + 既有生效路径」，与其他 bot 无异；W4 的 `APPLYING` 轮询态设计保留但不承载创建时序。

**范围修订（2026-08-31，用户拍板）——一期全部改为启动后下发**：`D4` 从 BaaS 系临时策略升格为全引擎一期策略。影响集中在 W4/W8/W13 边界：

1. **W8 一期范围收缩**：不做（issue 原 验收的）「teclaw 第一份 artifact 已含 manifest 结果」，**不触碰 `TeclawProvisionService` 创建序**。teclaw 一期的生效路径只剩一条：ACTIVE 后经 `TeclawDeviceFileSystem` 逐文件写（即 PUT 立即生效那条路）。W8 风险等级从「大且最险」降为中等；第 4 天 W6→W8→W9 的串行压力随之缓解。
2. **W4 两阶段形状保留、一期无挂接方**：A 阶段（script 直写 `ac_bot_startup_script`）在显式 apply 内依然先行执行，但其「必须先于 `_build_create_bot_payload`」的约束只有创建路径会触发——那已经推到二期。编排器形状（`apply(phases=...)` 可整可段）按 issue 要求原样保留，二期接 create 流时即插即用。
3. **首启断言收缩**：「首启时脚本先于 manifest 其余类目」断言取消（一期无任何启动前下发，断言无对照对象）；保留「无脚本启动命令 byte-identical」。「manifest script 不得依赖同 manifest 声明的内容」的文档警示**反而更要写**——脚本下次启动才生效，而 apply 失败不阻断就绪（§5），依赖的内容可能在、也可能缺。

## 3. 总架构：模块、存储、暴露面

### 3.1 模块布局

新模块一个，子包按职责切——所有跨模块依赖单向（adapters → api → core ← repository），core 不得 import api/adapters（既有一致性测试强制）：

```text
src/backend/src/agentclaw/community/core/bot_config_manifest/
├── README.md                      # Context Boundary（格式照 docs/arch/context-boundary-format.md）
├── manifest_schema.py             # W1: schema v1 文档模型+校验（防重复:引用不复制 manifest-schema.zh-CN.md 的字段表）
├── capabilities.py                # W1: 引擎×bot类型 → 逐类目支持表,单函数两入口(见 §4.1)
├── protocols.py                   # 模块内 Protocol: ApplyMaterializer / CredentialInjector / ContentSource
├── services/
│   ├── manifest_service.py        # W1: 存取+校验+all-or-nothing PUT
│   ├── source_credential_service.py   # W3: 凭证 CRUD+前缀授权判定
│   ├── content_store_service.py   # W11: 内容寻址存储+keep_last 读取路径+溯源
│   ├── apply_service.py           # W4: 两阶段编排器(bot 级串行化→锁)
│   └── apply_report_service.py    # W4: 报告存储+last-apply 读取
├── fetch/
│   ├── guarded_fetcher.py         # W2: SSRF 防护传输(见 §4.2)
│   ├── unpack.py                  # W2: zip/tar.gz 安全解包
│   ├── credential_injection.py    # W2: Protocol 声明;W3 绑定实现在 source_credential_service 侧(构造请求头时回调)
│   └── git_source.py              # W7: 浅层单 ref fetch + named-source 解析
└── materializers/
    ├── mcp_materializer.py        # W4(免取源: 注册表引用→既有启用+配置服务)
    ├── script_materializer.py     # W4(免取源: → BotStartupScriptService)
    ├── skills_materializer.py     # W5 → LocalSkillUploadService + DirectActivationService
    ├── identity_materializer.py   # W5 → IdentityService.write_identity_file
    ├── resources_materializer.py  # W6 → 既有资源服务(锚点见 §4.7)
    ├── named_source_resolver.py   # W7: sources/from 互斥+消化引用图
    └── cli_tools_materializer.py  # W9: ELF 校验+逻辑工具目录+PATH
```

仓储与暴露面**不在**模块内（仓库惯例）：

- 仓储协议/实现：`core/repository/{protocols,implementations}/bot/`（新增 4 对，见 §3.2）
- 服务 API 契约：`community/api/` 对外 Protocol，注册进一致性 `tests/community/architecture/test_service_api_conformance.py:227` 的 `_PAIRS`（架构测试强制 api↔core 成对一致；**注意 `_PAIRS` 是测试文件里的注册表，不是 api/ 层的运行时对象**）
- 路由：`adapters/http/openapi_v1/`（`bots/router.py` 追加 config-manifest 组；新增 `adapters/http/openapi_v1/source_credentials/router.py`），每条路由一行 `ADMISSION`（registry 在 `adapters/http/openapi_v1/admission.py:47`，未登记路由被 `dependencies.py:260` 的 admission 校验拒绝，`test_principal_seam.py` 钉死）
- 鉴权依赖沿用 `_GRANT_CHECKED_OWN_BOT` 模式（`bots/router.py:169` 现成样例是 `get_bot_startup_script:1322`）

### 3.2 存储模型（4 张新表）

键与租户隔离推理全部照抄 `ac_bot_startup_script` 先例：`script_key` 编码样本 `implementations/bot/startup_script.py:40`（`sha256(env|entity_id|bot_id)` hexdigest :69），DDL 样本 `core/bot_startup_script/sql/2026_08_10_bot_startup_script.sql`（表尾 `UNIQUE KEY uk_tenant_script_key`）。

| 表 | 键 | 内容 | 工作项 |
| --- | --- | --- | --- |
| `ac_bot_config_manifest` | uk `(avernet_tenant, manifest_key)` | manifest JSON 存档（整份 PUT 替换）、`schema_version`、updated 信息 | W1 |
| `ac_source_credential` | uk `(avernet_tenant, name)` | `header_name`、`secret`（`enc:v1:` 密文）、`allowed_prefixes`、`type`（v1 仅 `header`，`oss_aksk`/`basic` 写入即拒）、`updated_at` | W3 |
| `ac_manifest_object` | uk `(avernet_tenant, content_sha256)` | 内容字节、size、`source_url`/`resolved_ref`/`fetched_at`、**凭证名（只有名）**、来源类别 | W11 |
| `ac_manifest_apply` | bot 级最近报告：uk `(avernet_tenant, apply_key)` | apply 报告头（trigger/result/started/finished/sources 段）+逐条 entries 作 JSON 列（设计 §7 形状）；`dry_run` **不写**此表 | W4 |

- `keep_last` 读 W11 的 `ac_manifest_object`（"一套机制"验收：W4 的条目快照与 W11 的对象表是同一条读取路径）。
- 逐条 entries 用 JSON 列而非子表：last-apply 整体读取是 v1 唯一查询形态，无逐条检索需求；少一张表少一对仓储。
- **凭证值在任何表里都不存明文，`ac_manifest_object` 溯源只存凭证名**（两处 issue 均为验收硬条款）。

### 3.3 公开面与网关（本仓库踩过的坑，硬规矩）

新增 10 条路由（W1 四条 + W3 四条 + W4 两条）：

```text
GET/PUT/DELETE /openapi/v1/bots/{bot_id}/config-manifest
GET  /openapi/v1/bots/{bot_id}/config-manifest/capabilities
POST /openapi/v1/bots/{bot_id}/config-manifest/apply
GET  /openapi/v1/bots/{bot_id}/config-manifest/last-apply
PUT/GET/DELETE /openapi/v1/source-credentials/{name}     (+GET 列表)
```

1. `bots.openapi.json` **手工增量编辑，禁止全量 regen**（历史事故：全量生成把手工维护的内容冲掉）。每加一条路由手动补 operation 条目。
2. **avernet/ocb 双侧同步**：gateway 的 `application.yaml` 路由配置与本仓库 `bots.openapi.json` 要同步改——ocb 仓在 `~/IdeaProjects/ocb`，一个 PR 改两仓（W1/W3/W4 三个 PR 都会踩）。
3. **endpoint gate 必补用例**：新增路由必须在对应测试处补齐（gate 会因缺用例挂）。
4. PR 标题按 root `AGENTS.md`：`<type>(backend): <concise outcome>`；正文 Problem/Solution/Validation 结构，标题即 squash commit message。

### 3.4 特性开关（W1 的"遮面"）

现状：仓库**没有路由级开关先例**——`SkillCenterFlags`（`core/skill_center/feature_flags.py:27`，`SC_*` 环境变量）+ DI 装配期取用（`di/modules/skill_center_module.py:844`）。因此 W1 不发明新形态，照 `SkillCenterFlags` 抄一个 `core/bot_config_manifest/feature_flags.py`：

- `BotConfigManifestFlags.from_env()` 读 `BCM_*` 环境变量；`api_enabled` 默认 **False**（singlebox/CI 测试显式置 True）。
- 实现位置放在 openapi_v1 路由的公共依赖里（`dependencies= [...]` 追加 `Depends(require_manifest_enabled)`——开关关=404，路由注册与 ADMISSION 照常存在，避免"登记与否"的第三态）；core 服务/DI 层不受开关影响，W4 起测试全走 core API。
- M3（W5 落地）后翻 True 上预发。
- 平台侧 apply 不在 ARCA/teclaw 容器内执行，本地/singlebox 形态也支持 manifest（比 #935 script 的支持面宽，capability 函数照实返回）。

## 4. 逐项工程设计

锚点基于当前 dev HEAD（9147fe741），实现时以符号名为准复核行号。

### 4.1 W1 — manifest 文档：存储、schema v1、能力、API（#1469）

- **落点**：`manifest_schema` + `capabilities` + `services/manifest_service.py` + `repository` 对（`ac_bot_config_manifest`）+ `openapi_v1/bots/router.py` 四路由 + 开关（§3.4）。
- **schema 解析**：manifest 文档用模块自有请求/响应模型（与 openapi_v1 既有 body 模型同风格）；校验规则逐条来自 #1469 验收（"每条拒绝消息指名违规条目"→错误对象列表，PUT all-or-nothing 聚合返回 422+逐条原因）。
- **能力解析器 = 同一函数两入口**：`capabilities.supported_categories(engine_type, bot_type) -> CategorySupport`——唯一纯函数签名，`GET …/capabilities` 与 PUT 校验、（外部的 W13 Passport preflight）三处调用同一实现。**不读 bot 记录**；未知引擎 → 全不支持（fail closed，先例 `layout_planner.py`）。服务层不得加第二份判定（读写必须同源——issue 验收原话）。
- **never-fail 读**：无 manifest 的 bot `GET` 返回 `{schema_version:1, sources:[], manifest:{六类:[]}}` 空文档。
- **script 正文往返**：`script` 住在 manifest JSON 文档列里**按原文存储，不规范化**（不重排引号、不动 `$(id)`、`{token}`——#1469 原例）；GET 读回 byte-exact 由"存档列只序列化反序列化、无清洗"保证。往返测试用例直接引 #1469 的三组字符样本。
- **嵌套/越界路径、`apply_once`、未知占位符、git 源带 digest、`from`/`content` 带 auth**：全部在 `schema` 校验层拒绝（清单照 #1469）。**注意**：`path` 嵌套禁令要跨条目比对（目录条目集合 vs 文件条目），纯 pydantic 单条校验不够，需要一个文档级校验 pass——设计为 `manifest_schema.py` 里的 `validate_document(doc) -> list[Violation]`，pydantic 只做单条形状。
- **测试**：模块级校验矩阵（每个拒绝条款一例+具名消息断言）、PUT all-or-nothing、空文档读、跨租户隔离（两租户同 `bot_id` 互不可见——租户守卫已注册模式照 `bot_startup_script` 测试）、`_PAIRS` 注册、ADMISSION/principal seam 用例、OpenAPI json 断言（手工增量后 spec 与路由同步）。

### 4.2 W2 — 带防护的 fetcher 与归档流水线（#1470）

- **落点**：`fetch/guarded_fetcher.py` + `fetch/unpack.py` + `fetch/credential_injection.py`（仅 Protocol）。
- **后端今天没有 SSRF 防护——这是新机件**。**移植参照**是 engine 仓 `src/engine/src/engine/community/plugins/resource_materialization.py`（不 import、跨仓，抄结构与阈值思路）：URL 形状校验（scheme/host/禁 userinfo）→ DNS 全量解析且每个地址 `ip.is_global`（显式拒绝 loopback/169.254.169.254/ULA/multicast/reserved/RFC1918）→ **连接期 IP pinning**（连接已校验地址、保留原 Host header 与 SNI 防 check-then-rebind）→ `follow_redirects=False` 手动逐跳重校验（跳数上限）。
- 与 engine 先例的差异点（W2 验收更严）：http 仅部署级白名单放行；`sha256` 校验失败=拉取失败（"损坏的成功"是验收反面）；**逐条目字节上限在流式过程中强制**（content-length 预检+流式累计双卡，engine 先例已有同款）；超时/总预算/并发预算由调用方（W4 编排器）下发给 fetcher 的 `FetchBudget` 值对象。
- **解包**：zip/tar.gz 白名单；拒绝绝对路径/`..` 穿越成员/逃出根的 symlink-harlink 设备成员（zip-slip 全家桶）；成员数与解压后总大小上限（照 schema §5 限额取常量，magic number 禁忌→同一文件内命名常量区）；`strip_components` 无自动探测（相同输入必须相同表现）；统一 `chmod a-x` 抹可执行位（含 `.tar` 里的 mode 位与 zip 的 external_attr）。
- **接口**：`GuardedFetcher.fetch(request: FetchRequest) -> FetchedObject`（`FetchedObject` 含 bytes/sha256/source 元数据，"只写或只哈希，绝不执行"）；`CredentialInjector` Protocol 声明在 W2、W3 绑定（注入点在构造请求头，`allowed_prefixes` 判定在绑定侧）。
- **测试**：防护矩阵（内网 IP 全家/重绑定/重定向跨界/超限截断/digest 失败）用本地 mock HTTP + 可控 DNS resolver 注入（不依赖真实网络）；解包矩阵（zip-slip 每变体/权限抹平/strip 边界）。**这一项的测试本身是安全审计证据——按"可被安全 reviewer 复核"的标准写。**

### 4.3 W3 — 租户级源凭证（#1471）

- **落点**：`services/source_credential_service.py` + `ac_source_credential` 仓储对 + `openapi_v1/source_credentials/router.py` 四路由（PUT/GET/DELETE 单名 + GET 列表）。
- **加密不新增**：`TokenVault`（`core/bot_management/token_vault.py:21`）+ `utils/secret_utils.py:85 symmetric_encrypt`（AES-GCM，`enc:v1:` 前缀 `token_vault.py:18`），主密钥经 `SecretResolver`（`plugin_api/secret_resolver.py:14`，community 实现读 `AGENTCLAW_SECRET_*`）。
- **fail-closed 守卫（本项新增，验收硬条款）**：`TokenVault.encrypt` 在 master key 为空时明文直落（`token_vault.py:33-34`，解密侧 `decrypt_or_passthrough:37-46` 无前缀透传）——这是 singlebox/CI 的刻意行为（`plugins/local/secret_resolver.py:38` 返回 None → 空 key）。守卫加在**凭证写入服务层**：生产 profile 标志下（profile 判定沿 `di/profile_modules.py:140` 的形态，经 DI 注入一个 `credentials_fail_closed: bool`）master key 解析失败 → PUT 422 拒绝。测试用两 profile fixture 钉"生产空 key 拒写/singlebox 空 key 放行"。
- **前缀授权**：`allowed_prefixes` 必填、绝对 https 前缀、**路径段边界匹配**（`== prefix` 或 `startswith(prefix + "/")`；整 origin 显式 `https://host/`）；实现为 `matches(target) -> bool` 纯函数+表驱动测试（`…/team/content` vs `…/team/content-secret` 必negative——issue 原例）。目标在所有前缀外 → 该条目 failed（**绝不裸连**——fallback 不带凭证这一行为本身就是验收反面）。
- **重定向跨界**：在 W2 fetcher 预授权判定点做——每跳重定向发生时先 query W3 的判定服务，跨界=拉取失败（凭证不跟随、不剥离继续）。W2/W3 接口：fetcher 接受 `AuthorizationPolicy` 回调对象。
- **读面**：GET 只回 `has_secret/header_name/allowed_prefixes/updated_at`（掩码模式照 `openapi_v1/mcp/router.py:452` + `core/mcp/presentation.py:23 mask_api_key` 同一生命周期形态——"读时掩码用时注入"是仓库既有能力类别，不是新增）。
- **类型判别从第一天是真的**：`oss_aksk`/`basic` PUT 即 409（存储形状留给将来）。
- **具名认证失败**：fetch 层捕获 401/403 包装成 `CredentialRejectedError(name)`，apply 报告里区别于通用拉取错误。
- **轮换**：PUT 同名即覆盖，不触发 apply（惰性口径）。**删除被引用的凭证**：删除放行（W3 范围外无引用检查阻塞），引用它的条目下次 apply `failed` 并指名"credential X 不存在"。

### 4.4 W4 — apply 引擎、apply 记录、免取源物化器（#1472）

- **落点**：`services/apply_service.py`（编排）+ `services/apply_report_service.py` + `ac_manifest_apply` 仓储对 + `openapi_v1/bots/router.py` 两条路由（apply/last-apply）+ `materializers/{mcp,script}_materializer.py`。
- **两阶段编排（反转设计 §3.4 原 script-last 顺序，issue 验收为准）**：
  - **A 阶段**（无容器）：仅 `script` → `BotStartupScriptService.put`（`core/bot_startup_script/services/startup_script_service.py:102`）。**范围修订后：一期没有任何路径把 A 挂进创建流**（挂接推二期,#1508），「必须先于 `_build_create_bot_payload`（`baas_service.py:622`）」的约束成为二期验收项；一期显式 apply 内 A 先 B 后一次跑完，script 当次写库、**下次启动生效**（#935 既有口径,响应文案要说明）。编排器保留按 issue 要求「可整可段」（`apply(phases=[...])`）的形状,否则二期的 W13/W8 只能绕开它（issue 原话）。
  - **B 阶段（一期全部下发都在此后）**：`identity → resources → skills → mcp` 固定顺序，物化的实体落平台侧；对活 bot 走既有通道生效（见 W8）。
  - `ApplyService.apply(bot_id, manifest, trigger) -> ApplyReport`。
- **串行化**：bot 级锁**沿 `BotRestartLockRepository` 模式另写一对**（协议形状照 `protocols/bot/bot.py:386` / 实现 `implementations/bot/restart_lock.py:49`：`acquire(env, entity_id, bot_id, holder) -> Optional[lock]` + `release`，随机 `lock_token` fencing 持久行）——**不改既有表**（restart 锁与 apply 锁语义不同：前者护重启复合流程，后者只在 apply 重入间互斥；且持锁期要求可判 owner）。锁仓储落在 `ac_manifest_apply` 一侧的独立锁表 `ac_manifest_apply_lock`（uk 同构）。**不改 `BotRestartLockRepository` 本身**——issue 说"沿用模式"指的是 fencing 语义,不是共用表。
- **覆盖语义（D2）落实为物化器接口契约**：`Materializer.apply_category(bot, declared_entries) -> CategoryResult`——类目 all-or-nothing（任一物化失败=整个类目不覆盖，原内容零损伤——**这是全计划最重要的一条测试**：声明 {A,B}、B 失败，B 现存内容完好）；区域逐类目（resources 只盖声明 path 子树）；保留名 `MEMORY.md`/`IDENTITY.md` 永不写永不删（独立测试钉死）；`skills: []` 删全部含 UI 装的。
- **逐条分类** `created/updated/unchanged/skipped/failed`，`skipped`="所在类目被中止"；收敛性测试：同文档 apply 两次，第二次全 `unchanged` 且**零写入**（仓储调用计数断言——坑：coverage 钉住计数与 gate 冲突时以断言语义优先）。
- **`on_fetch_failure` 只 `keep_last|fail`，`skip` PUT 拒绝**（覆盖语义下 skip=删条目，字面相反）。
- **`DELETE` 什么都不删**（`[]` 是声明、缺席是无意见——一个测试同时钉两个行为，issue 点名"值得一个测试"）。
- **dry_run** 返回完全形态报告但**不落任何存储**（含 apply 报告表——验收原话"包括写报告存储"）。
- **W10 缝的单点接线**：`ApplyService` 不直接调 openapi_v1 的 router 层校验；它依赖一个 `ManifestGuards`（Protocol，形状=W10 交付的服务层缝——归属/授权裁定、包载荷/路径校验的声明式依赖）。W10 未合并前该 Protocol 只有接口与测试 fake；合并后绑定实现。**这是有意的唯一一处等待点**。
- **mcp 物化器**：`server_code` 注册表引用 → 走既有 MCP enable/配置服务（`core/mcp/config_flow.py:83 read_unified_config` 一族写路径照 `config_flow.py:116/162/172` 形态）；**MCP 凭证照旧平台持有、compose 时内联，manifest 侧零新增**。
- **script 物化器**：A 阶段直调 `BotStartupScriptService.put`；teclaw/desktop 引擎拒绝是 #935 `_support.py`（`resolve_support:152`）既有口径,复用不重写。
- **报告**：`trigger` 枚举 `create|republish|restart|explicit`；sources 段只有命名源（W7）；凭证**只记名**（W4/W3 双验收）。`GET …/last-apply` 整表最近一行。

### 4.5 W11 — 平台侧物化与留存（#1510）

- **落点**：`services/content_store_service.py` + `ac_manifest_object` 仓储对。无路由（纯内部服务）。
- **写入与去重**：W2 的 `FetchedObject` 落库——`content_sha256` 内容寻址天然去重（命中即复用整份对象,含溯源覆盖更新）；`keep_last`、下发、审计三家共用这条读取路径（"一套机制不是两套"验收）。
- **下发从存储读**：物化器拿到的是 store 引用而非网络结果——重试下发绝不会重新拉取、源侧故障不污染进行中的下发（测试：拉取成功后断网，重试 apply 仍 `unchanged`）。
- **溯源字段**（audit 硬要求）：source、resolved ref/SHA/digest、fetched_at、字节本体、**凭证名**。"留存对着审计要求显式陈述"——README 里写明留存语义：内容无限期（内容寻址、去重、被引用即留）；`keep_last=1` 是**条目级语义**（对每个 manifest 条目保留最近一次成功物化的对象引用），不是全表轮换；本期**不做**时间窗清理（v2 评审再上）。
- **大小**：对象体受 W2 流式上限与 schema §5 限额双重约束（常量同一个，不复述两处）。DB blob 存 v1 拍板：上限内的 zip/文件直落 `LONGBLOB`，manifest 限额本身就把它挡在安全区间；OSS 化留 v2（对照 `2026-07-09-teclaw-artifact-oss-offload` 的既有 OSS 先例）。

### 4.6 W5 — 从 URL 源装 skills 与 identity（#1473）

- **落点**：`materializers/{skills,identity}_materializer.py`，挂进 W4 的 B 阶段序（identity → skills）。
- **skills**：**必须**走 `LocalSkillUploadService`（`core/skill_center/services/local_skill_upload_service.py:68`）+ `DirectActivationService`（`direct_activation_service.py:59`）——"DB 行+文件、激活走正规服务"是硬规则（design §3.3：裸丢文件会被 skills-pool reconcile 枚举进 pool 而无记录）。验收测试三连：与手工上传同 skill 不可区分、reconcile（`SkillsPoolReconcileService.reconcile`，`reconcile_service.py:86`）存活、quarantine 不误清（`cleanup_quarantine` 路径对 managed 实体行为有覆盖）。
- **非 git skill 源必须 digest**：`PUT` 即拒（schema 校验层）——"skill 携带代码，未钉扎 URL 每次拉到的是当时那里有的东西"。
- **identity**：走 `IdentityService.write_identity_file`（`core/services/identity.py:400`，内部经 `resolve_runtime_engine_for_bot:414` 定引擎、`_device_write:345` 落设备）；`type` 写入时校验对引擎白名单（`validate_file_type:214`/`CLAUDE_CODE_IDENTITY_FILES:80`——**复用它的判定，不在 manifest 层重写**）。内联 `content` 支持；内联带 `auth/digest/on_fetch_failure` 拒绝。
- **`${OCB_*}` 变量替换**（变量名以 schema §4 为准：`OCB_BOT_ID`/`OCB_ENGINE_TYPE`/`OCB_ENV`/`OCB_TENANT`；W9 追加 `OCB_BOT_ARCH` 常量 `amd64`——issue 行文里写作 `${BOT_*}`/`${BOT_ARCH}` 是同一组东西的简写）：时机=拉取前&前缀授权前（替换结果出不去 allowed_prefixes——负例测试：占位符展开后指向前缀外，期望 failed 不是越权拉取）。未知占位符写入即拒（W1 校验）。
- **归档/目录自动判定**：content type/扩展名启发+`unpack` 显式覆写。
- **类目独立性**：某条目拉取失败**只中止本类目**（类目级 all-or-nothing），其他类目照走、bot 照常启动——错误层级编码进 `CategoryResult`。
- **M3 开闸**：W5 落地后 `BCM_API_ENABLED` 翻开（§3.4），进入业务可见状态。

### 4.7 W6 — resources：文件与目录（#1474）

- **落点**：`materializers/resources_materializer.py`。
- **⚠ 开工首日锚定任务**：**"既有资源服务"当前未定位**。候选按序探索：①workspace/文件下发服务（session-file/engine-content 系设计遗留的文件通道，先看 `core/services/` 与 device 写路径）②`IdentityService` 的写通道模式（`_identity_device_fs:262` + `_device_write:345`——文件级设备写已有坐标样板）。**决策规则**：优先既有服务；若只有文件级通道，则 resources 物化器经 `DeviceFileSystem`（`core/devices/services/device_filesystem.py:27`）组合——这仍满足"只经既有 core 服务"铁律（DeviceFileSystem 本身就是 core 服务，IdentityService 也是这么做的）。**把此锚定作为 W6 spec 目录里的第一个 task，产物补进本文。**
- **目录条目收敛单位=整个归档**：内容 digest 未变 → `unchanged` 零写（整树级比较=归档内容哈希，**不做**逐文件 diff）。
- **目录所有权替换**：内容变=临时目录解包+原子 rename（temp+rename 照 skills-pool 先例）；`path` 下旧树整体消失**含 bot 手工文件**、`path` 外一律不动（两方向都要测试：树内手工文件消失☑ / 树外文件存活☑——issue 原例）。
- **schema §5 归档限额**（单归档/解包总大小/成员数）三处复检（PUT 校验+apply 复查+解包器强卡）。
- **teclaw**：物化树逐文件展成 `FileRef`/`ResourceRef` 条目装进**现有** `BotConfigArtifact` 词汇表（`kernel/bot_config/artifact.py:175`，`FileRef:97`）——v1 不做子树优化（T5 排除）。
- **嵌套禁令**：PUT 强制+apply 复查（W1 schema 已做，这里只消费其结果）。

### 4.8 W8 — 生命周期 apply 点（#1476）

> **范围修订（2026-08-31，见 §2）**：一期取消「teclaw 第一份 artifact 已含 manifest 结果」这条 issue 验收（推二期，#1508）——**本项不再触碰 `TeclawProvisionService` 创建序**，只挂 bot **已存在**的生命周期事件。一期 teclaw 的生效路径 = ACTIVE 后 `TeclawDeviceFileSystem` 逐文件写（与 PUT 立即生效同一条通道）。交付前在 #1476 comment 说明范围修订，避免验收口径对不上。

- **落点**：不改编排器，把 `ApplyService` 挂进生命周期——只触碰 `core/service_bot/services/bot_publish_service.py`（publish/republish 流与重建式 restart 的 apply 点）；`create_flow`/Passport 段（外部 W13）与 `TeclawProvisionService`（创建序）**均不在一期范围**。
- **PUT 立即生效不重启（issue 逐条）**：
  - BaaS/ARCA：`identity/resources` 走 `DeviceFileSystem` 写；`skills` 进 active skill set 由既有全量 symlink 对齐收敛，经 `DeviceSyncDispatcher`（`plugin_api/device_sync_dispatcher.py:33`）/`sync_symlinks`（`core/devices/services/device_sync.py:36`）触发。
  - teclaw：`TeclawDeviceFileSystem`（`core/devices/services/teclaw_device_filesystem.py:45`）逐文件写直接转发引擎；`TeclawDeviceSyncPlugin.sync_symlinks([])`（`ChannelService` 在用）**可用但要先确认哪些类目真的需要**——不默认伸手拿（issue 原话）。
- **teclaw 永不 `BotService.restart_bot`**（`bot_service.py:4291` 抛 `BotOperationNotAllowedError`；publish 侧注释：销毁容器→重分配失败→bot 无 binding 坏状态）。两个引擎系都不需要重启——测试还原一条"teclaw 上调 restart 的调用即失败"守卫断言。
- **script 立即下发、下次启动生效**，响应文案说清楚（延后是 §2.7 的边界，不是例外）。
- **§2.7**：apply 不写 bot 记录、不按首启分支、纯拉取态（无通知）；`last-apply`+（外部的 W13）轮询返回级明细足够自查"生效了吗"。
- **扩容不 re-apply**：实例一致性=共享平台状态（#926 真正诉求）——回归测试断言 scale-out 路径上 apply 调用次数=0。
- **启动顺序断言（范围修订后）**：**保留**「无脚本启动命令 byte-identical」（#935 既有断言原样保留）；**取消**「首启时脚本先于 manifest 其余类目」断言——一期无任何启动前下发，该断言无对照对象（issue 原文的这条与 #1508 一起归二期，二期做启动前下发时重建）。**文档警示保留且升级**："manifest script 不得依赖同 manifest 声明的任何内容"——脚本下次启动才生效，而 apply 失败不阻断就绪（§5），声明的依赖在脚本运行时**可能在、也可能缺**，这条从时序契约变成健壮性契约。
- **风险姿态（修订后降级）**：规模中、风险中——一期只触 `publish_flow` 一处深水区（republish/重建点的 apply 调用与租户上下文传递，`bind_current_avernet_tenant` 线程模式照 `bot_publish_service.py:1291`），`TeclawProvisionService`/创建序完全不动。teclaw 臂与 BaaS 臂可同一 PR 或分臂交付（W12 不再卡一期）。二期做启动前下发时风险回升（create 序+首份 artifact+A 挂 payload 三件一起上）。

### 4.9 W7 — 命名源与 git 源（#1475）

- **落点**：`fetch/git_source.py` + `materializers/named_source_resolver.py` + schema 的 `sources`/`from` 互斥（W1 校验已备）。
- **X1 形态**：`{git repo_url, ref, auth}` → 浅层单 ref fetch；机器账号 token `read_repository`、HTTP Basic 注入（注入经 W3 `CredentialInjector` 绑定 git 变体）。**不做归档 API**（取代 design §10.5）。技术选型：纯 Python `dulwich` 浅 fetch（进程内、无 hook 执行面、`depth=1` 单 ref——fetch 天然不执行服务端 hooks，但 dulwich 把"不会跑 hook/filter"从既有约定变成本地事实）；备选手写 smart-HTTP packfile 拉取在 dulwich 不达验收时再启——**开工首日做 30 分钟 spike 后拍板**。
- **每 {git, ref} 每 apply 拉**一次**：resolver 维护同源实例缓存（一次解析的 SHA+树供所有引用条目复用——并行安全：apply 内串行取用即可）。
- **原子升级**：ref 解析 SHA 记入报告 sources 段（`ref`+`resolved_sha` 双记，§7 形状）；tag 重指下次 apply 收敛（D2 语义自然成立）。
- **迁移的 tag**、`git 源禁 digest`（W1 已拒）、目录条目免 `unpack/strip_components`（schema 侧规则）。
- **砍单顺位第二**：v1 纯 URL 源可跑——若 D6 前进度告急，此项整体后置不并测试债。

### 4.10 W9 — cli_tools（#1477）

- **落点**：`materializers/cli_tools_materializer.py` + teclaw 契约（`teclaw-cli-contract.zh-CN.md` 已在 dev（commit 898ad7ef6），直接分享给 teclaw owner）。
- **验收要点**：digest 强制+作为收敛判据；**ELF 头校验**（自写小型只读解析器：`EI_MAG`+`e_machine==x86_64`+存在 `PT_INTERP` 即拒绝为"非静态"——错误二进制 digest 仍然有效,digest 管"是不是你要的"、ELF 管"能不能跑"，两者独立都要卡）；仅静态二进制+归档两形态（包管理器安装=script 领域）；平台定义逻辑工具目录进 PATH（物理路径不透给用户）；按引擎默认技能集放工具用法 skill（`SkillSetService.ensure_default_skill_set:1106`——engine_type 维度机制现成）。
- **首个消费者 `bcs-cli`** 取代 singlebox 手工放置脚本（脚本位置**开工首日定位**——所以历史记都指它属 singlebox 编排）。
- **砍单第一顺位**：teclaw 半边已可独立交付（契约文档），平台侧可整体后移。

## 5. 横切设计

- **失败策略**（design §4.3）：`keep_last`（默认）/`fail` 段级——**`skip` 已被 D2 撤销**（PUT 拒）。apply 失败**不阻断** bot 就绪（与 #935 口径一致,teclaw strict 门控是 v2 项由 W12 契约接续）。
- **安全边界汇总**：SSRF（W2 pinning+白名单）/ 凭证零泄露（W3 加密+掩码+前缀+fail-closed;W4/W11 只记名）/ 解包爆炸（W2 成员/大小上限+权限抹平）/ 供应链（W5 skill digest 强制;W9 ELF+digest 双卡;W6 限额）。安全评审触发点：W2/W3/W4 三个 PR 落地时各跑一轮 `security-reviewer` 视角复审（本仓库 CLAUDE 规则对"外部输入/凭证/加密"是 mandatory review）。
- **可观测性**：apply 报告=唯一一等记录（§7 JSON 形状:apply_id/trigger/sources/entries/分类+error）。**凭证值永不入报告**。指标本期不加（纯拉取读取模式）。
- **租户隔离**：四表全带 `avernet_tenant` 列+租户守卫注册（照 startup_script 模式）；W13 备注的 `bind_current_avernet_tenant` 陷阱已知——`threading.Thread(target=bind_current_avernet_tenant(fn))` 在构造点捕获（先例 `bot_publish_service.py:1291`），**绝不**当装饰器用于模块级函数。

## 6. 测试与 CI 硬规矩（仓库既有事实）

1. `src/backend/scripts/ci_test.sh`：行覆盖 `BACKEND_CI_LINE_COVERAGE_MIN`（默认 75）+ 变更行覆盖逻辑（PR gate 以本地复现为准）；**触 apply 路径（W4/W5/W6/W8）的 PR push 用 `OCB_PRE_PUSH_RUN_CI=1`**，其余 pre-push 只跑 lint。
2. 测试目录镜像 src：`tests/community/core/bot_config_manifest/…`；架构测试 `_PAIRS`（`test_service_api_conformance.py:227`）新增 4 对协议+实现；principal seam 测试会**拒绝未登记 ADMISSION 的路由**——每条路由的用例与 ADMISSION 行同一 PR 内成对出现。
3. 仓库规则 coverage≥80% 靠本地 `ci_test.sh` 自证，PR 的 Validation 节粘贴结果摘要（含用例数与耗时）。
4. 每项验收标准→测试映射表随各项 spec 目录（`src/backend/specs/<yyyy-mm-dd>-<slug>/{spec,plan,tasks}.md`,范本 `2026-08-10-bot-startup-script/`）发行时固化——**本文档只定"必钉死测试"清单**（跨项不变量）：
   - W4 类目中止不伤既有实体（B 失败 A 完好）
   - W4 空 `[]` vs `DELETE` 双行为
   - W4 二次 apply 全 `unchanged` 零写入
   - W4 `MEMORY.md`/`IDENTITY.md` 例外
   - W6 `path` 内手工文件清除 / `path` 外存活 双向
   - W5 manifest skill ≡ 手工上传、reconcile 存活
   - W8 无脚本启动命令 byte-identical + teclaw 永不 restart + 扩容零 apply
   - W3 前缀段边界负例（content vs content-secret）
   - W2 内网地址/重绑定/超限/损坏 digest 安全矩阵
   - W1 跨租户同 bot_id 隔离

## 7. PR 列表（10 个，标题按 root AGENTS.md）

| # | 工作项 | 标题 | 里程碑 |
| --- | --- | --- | --- |
| 1 | W1 | `feat(backend): add bot config manifest document storage, schema v1 and capabilities API` | M1 |
| 2 | W2 | `feat(backend): add guarded fetch and unpack pipeline for manifest sources` | M1 |
| 3 | W3 | `feat(backend): add tenant-level source credentials with prefix authorization` | M1 |
| 4 | W11 | `feat(backend): add content-addressed manifest object store and retention` | M2 |
| 5 | W4 | `feat(backend): add two-phase manifest apply engine with records and fetch-free materializers` | M2 |
| 6 | W5 | `feat(backend): materialize manifest skills and identity from pinned URL sources` → 开闸 | M3 |
| 7 | W6 | `feat(backend): add manifest resources with atomic directory ownership` | M4 |
| 8 | W8 | `feat(backend): wire manifest apply into publish and rebuild lifecycle points` | M4 |
| 9 | W7 | `feat(backend): add named and git sources with atomic ref upgrades` | M5 |
| 10 | W9 | `feat(backend): add manifest cli_tools with ELF validation and PATH injection` | M5 |

每 PR 正文 Problem/Solution/Validation + Spec 节指向该项 spec 目录；issue 关闭映射（`Closes #1469` 等）按 PR 落点放；**PR1-3 落公开面时同步 ocb 仓**（§3.3）。

## 8. 风险与未定项（显式清单）

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| W10（缝）不按期 | W4 延迟 → 关键链整体后移 | 开工日催收；W4 先落骨架+`ManifestGuards` fake；绝不自写第二份校验 |
| W6 "既有资源服务"未锚定 | W6 落点漂移、范围可能扩大为大件 | 开工首日锚定（§4.7 两个候选+决策规则）；锚定产物回写本文 |
| 6 人日预算 vs 24 人日原评 | 全序列真实工期 ≈2 周 | 砍单台阶已备（W9→W7→W6）；M3 即业务可演示点 |
| publish 流回归面（W8，修订后仅剩 publish 一处深水区） | publish/republish/重建点回归成本 | 独立成 PR+半天专项回归；`TeclawProvisionService` 一期不动 |
| 二期风险前置记录：启动前下发三件套（teclaw 首份 artifact、create 内 apply、A 挂 payload） | 二期工作量与排期在开工前需重新评估 | #1508 跟踪；W4 编排器形状已按可复用保留 |
| 范围修订与 issue 验收口径漂移（#1476 的"第一份 artifact"条、#1696 的首启承诺） | 验收对不上引发返工误解 | 交付 comment 显式说明修订来源与二期去向；修订已记入本文 §1/§2 |
| 单人连续上下文切换（10 项跨 3 个子系统） | 认知负荷 | 每项独立 spec 目录+严格按序开工,不做跨项并行 |
| dulwich 选型不确定（W7） | fetch 实现路线返工 | 30 分钟 spike 先行（§4.9） |
| 开关遮面期间路由已公开注册 | 未开闸前 API 404 | 验收就是"意料中"——单测钉 404 形态；预发开闸前人工核 |
