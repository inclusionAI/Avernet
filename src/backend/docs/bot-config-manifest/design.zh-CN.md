# Bot Config Manifest 设计

> 状态：DRAFT（讨论稿）。术语与文档地图见 `README.zh-CN.md`。

## 1. 背景与动机

#935 为 bot 引入了 per-bot startup script：脚本存在 bot 上，容器每次重建时随
启动命令执行，解决了 #926「手工装进容器的东西在重建后丢失、scaled bot 无法让
实例一致」的问题。

业务随即提出下一步需求：**TC Open API 已有的能力（上传 skill、上传 identity
文件等）希望在容器启动时自动完成**。典型场景：用户的内容（skill zip、人设
md）维护在他们自己的服务上，希望 bot 每次拉起时自动取最新版装好。

直接让用户在脚本里操作是不可接受的，原因有二：

1. **目录结构不能暴露**。每个引擎的工作区、skills 目录、identity 文件位置都
   不同（见 `layout_planner.py`——openclaw 的 active_root 是
   `.openclaw/workspace/skills`，claude_code 是 `.claude/skills`，等等），
   让用户写路径等于让用户为每个引擎写分支逻辑。
2. **teclaw 必须支持**（业务明确要求），而 teclaw 在任何一层都没有容器内执行
   通道：BaaS 的 `TeClawPaasService` 没有 `execute_command`，
   `_device_service` 对 TeClaw 平台直接跳过 `after_create_cmd_hook`，
   `TeclawProvisionService` 不走 `apply_device`。它唯一的按 bot 配置通道是
   整包 `BotConfigArtifact`（引擎从 OSS 拉取），且该契约明文规定「物理放置是
   引擎所有者的决定，ref 刻意不带布局提示」。

## 2. 方案总览

### 2.1 配置意图 = manifest + script

一个 bot 的启动配置意图存为**一份 bot 级文档**，含两个部分：

| 部分 | 性质 | 表达能力 | 引擎支持 |
| --- | --- | --- | --- |
| `manifest` | 声明式 | 「要什么、从哪来」：MCP、workspace 资源（含目录）、local skills、engine config、identity、CLI 工具（`cli_tools`，排期后置） | **所有引擎**（含 teclaw；`cli_tools` 在 teclaw 待确认 T4） |
| `script` | 命令式 | 任意 shell：沙箱内出网取数、条件逻辑、转换 | 能力门控：ARCA 系支持；**teclaw、desktop 拒绝**（沿用 #935 `_support.py` 口径） |

预期绝大多数需求（取内容→装上）能被带 `source` 的 manifest 吸收；script 只
承接无法声明化的长尾，且写入时即拒绝不支持的引擎（fail closed），而不是等
启动后静默不执行。

### 2.2 三层架构

1. **源文档层**：manifest + script，引擎中立，bot 级存储（租户隔离机制沿用
   `bot_startup_script` 的 `(avernet_tenant, script_key)` 模式），经
   TC Open API 读写。
2. **平台侧 apply 层（本设计的核心新增）**：在 apply 点评估 manifest——
   fetch 源内容（guarded fetcher）→ 通过与 TC Open API 相同的内部服务落成
   **真实平台实体**（skill 记录、identity 记录、MCP 启用配置、engine
   config、resource 记录）。
3. **交付层（零新增）**：平台实体到容器的下发完全复用现有机制——teclaw 走
   `BotConfigArtifact` 整包组装，ARCA 系走现有 push / NAS 通道；script 走
   #935 的启动链。

### 2.3 为什么 apply 落成平台实体（路线 B），而不是 compose 时虚拟合并（路线 A）

路线 A（组装 artifact / 启动命令时临时评估 manifest，结果不落库）会造成两套
真相：UI 的 skill 列表看不到 manifest 装的 skill；skills-pool 的
reconcile / quarantine 机制不认识它们，**可能把它们当作 drift 清理掉**。

路线 B 一次解决三件事：

- 平台视图 = 容器实况，reconcile 天然认识这些实体；
- teclaw 侧工作量归零：artifact 组装管线照旧读平台状态，连「新增一个组装
  输入」都不需要；
- ARCA 系声明式部分也不需要容器内动作，声明式 manifest 成为纯平台侧能力，
  引擎侧不需要任何新增实现。

**硬性规则：apply 只允许通过既有 core 服务写实体，永远不允许绕过它们直接写
文件系统或设备。** 这是防止状态分叉的那道闸，实现评审时按此检查。

### 2.4 备选方案与取舍

**备选一：跨引擎统一的「脚本 + 原子操作 CLI」（script 为唯一载体）。**
容器内预装 op CLI（`install-skill` 等），用户脚本调用，各引擎实现 CLI。
对 ARCA 系可行（也保留为 v2 的 script 体验增强，见 §9），但作为唯一方案被
否：teclaw 没有执行通道，强推等于要求 teclaw 新建一个容器内执行阶段，工程量
完全不同量级，且违背其 artifact 契约的核心决定。

**备选二：script 作为 artifact 字段下发给 teclaw。** 换信封不换本质，三个
硬伤：① teclaw 仍需新建执行器；② artifact 是整包重投的（update、restart
重拉），装状态声明可以收敛，装一段 bash 则每次重投重跑副作用，幂等责任全部
甩回脚本作者；③ #935 的安全论证（base64 / `su admin` / `__OCB_RC` /
`mktemp`）针对 ARCA 启动链，塞进 teclaw 等于要求对方在新上下文里重新发明并
验证一套等价机制。

**备选三：对已启动的 teclaw bot 逐个重放 op（平台调用其运行时 API）。**
① 平台需维护可恢复的编排状态机（打到第几个 op、失败从哪重试），每个 op ×
每个重试点都要求幂等；② 不是批量语义，且有真实竞态窗口——bot 已 ACTIVE 在
接消息，identity 还在逐个落地，可能用未配置好的人设先回了消息。配置必须在
就绪**之前**到达，本方案通过「apply 先于 compose、compose 先于就绪」保证。

**备选四：启动时跑一个 Skill，输入为自然语言。** 不能作为基础层：
① #926 的立项动机就是实例一致性，LLM 每次启动解释一遍自然语言，N 个实例
可能得到 N 种状态，恰好重新引入要消灭的问题；② 容器就绪靠 exit code 门控，
「agent 认为自己完成了」不是可门控状态；③ 自举顺序——跑 skill 依赖引擎已
配置好，而Bot 配置清单干的正是配置引擎；④ 每次启动付 token 成本与 agent 时延
（现有 user stage 预算 300s）；⑤ 审计与回放困难。但两者不冲突：确定性配置
层建成后，可以再提供官方 skill 封装此能力面向对话场景——NL 层建在确定性层
之上成立，反之不成立。

## 3. Apply 语义

### 3.1 Apply 点

| 事件 | 是否触发 apply | 说明 |
| --- | --- | --- |
| bot 创建 | ✅ | ARCA 系：在 compose 启动命令之前；teclaw：在组装首份 artifact 之前。**bot 拿到的第一份配置即已包含 manifest 结果，不存在「起来之后再补打」** |
| publish / republish | ✅ | teclaw 的主要 apply 点（artifact 重组装） |
| 重建式 restart（destroy-and-create） | ✅ | 与 #935 script 的重跑边界一致 |
| scale-out | ❌ | 复用上次 compose 的结果。实例间天然一致：所有实例共享同一份平台状态 |
| manifest PUT | ❌ | **惰性生效**，与 #935「PUT script 不影响活容器」口径一致 |
| `POST …/config-manifest/apply`（显式） | ✅ | 用户要「立即生效」时用。落成实体后经各 provider 的**既有**通道生效——对活的 teclaw bot 即其既有逐文件通道（与今天手工调 open API 的生效方式一致；`TeclawDeviceFileSystem` 现状即「活容器是文件真相源，编辑不整包重投」），artifact 重组仍只发生在既有晋升点 |

### 3.2 GitOps 语义：声明获胜、漂移纠正

manifest apply 落成的实体有两个写入方（apply 与用户手工 API/UI 编辑），
优先级必须定死，否则行为未定义。规则：

1. **manifest 声明过的实体：声明获胜。** 每个 apply 点重新 fetch 源、覆盖
   现状。用户想改内容，改源头（自己服务上的文件）或改 manifest——不是在
   UI 里直接改。
2. **manifest 未声明的实体：完全不碰。** 手工上传的其他 skill、其他
   identity 文件对 apply 不可见，两种管理方式共存、边界清晰。
3. **`managed by manifest` 标记**：manifest 管辖的实体打标记并在 UI/API
   可见（「由 manifest 管理，手工修改将在下次启动时被覆盖」），把覆盖行为
   从「平台吞了我的数据」的工单变成讲得清的契约。

为什么默认必须是声明获胜：假设手工获胜，scale-out 时老实例带着手调过的
SOUL.md、新实例从 manifest 长出来，同一个 bot 两个实例人设不同——这正是
#926 要消灭的。存下来的声明是真相，每次启动从真相再生。

逃生舱（v2 预留，不进 v1）：条目级 `apply_once: true`，仅在实体缺失时创建、
之后手工修改保留。默认永远是声明获胜。

### 3.3 幂等 → 收敛

命令式方案里幂等是每个操作、每个重试点的义务；声明式方案里它被替换为
**收敛**：「让实际状态匹配文档，同一份文档应用 N 次 = 一次」。

- 平台侧：apply 本身按此实现（比对声明与现状，必要时才动作），条目结果分
  `created / updated / unchanged / skipped / failed`。
- teclaw 侧：**不是新增义务**。artifact 本来就是整包重投的（update、restart
  重拉都会把同一份文档再给引擎），其契约今天就隐含收敛应用；本设计只是把它
  显式写为契约（见 `engine-requirements.zh-CN.md` 确认项 T2）。

### 3.4 类别间顺序与并发

> **⚠️ 第一期已推翻本节的顺序（W4 已实现，见 work-items §2.12）。**
> 本节把 `script` 放在最后，理由是「script 可以依赖 manifest 声明的实体已经
> 就位」。**实现里 `script` 排在最前**，而且这不是偷懒：`script` 物化成
> `ac_bot_startup_script` 的一行，而 `BaasService._build_create_bot_payload`
> 在拼装启动命令时读这一行——所以在创建路径上，这一行必须在 payload 构造
> **之前**就存在，否则首启根本不带脚本。其余类目都要解析 `DeviceFileSystem`
> 或设备上下文（未绑定即抛），只能在容器起来之后下发。
>
> 于是编排器是**两阶段**的：A 阶段只有 `script`（不需要容器），B 阶段是
> `identity → resources → skills → mcp`（需要容器）。在已运行的 bot 上两段
> 紧挨着跑，切分不可见；在创建路径上它们被整个容器开通过程隔开，这正是 W13
> 需要这个形状的原因。
>
> **代价直说：**首启时 script 在其他类目**之前**运行，所以第一期规定
> **manifest 的 `script` 不得依赖该 manifest 声明的任何内容**——与本节原文
> 相反。#1508 在第二期通过启动前下发消除这个限制，届时本节的顺序可以恢复。

- 单次 apply 内固定顺序（**原设计，已被上面的两阶段取代**）：
  `engine_config → identity → resources → skills → mcp`。script（若有）在全部
  交付完成后、启动链末端执行。
- 同一 bot 的 apply 串行化（bot 级锁）；apply 收敛性保证并发触发（如显式
  apply 撞上 republish）最终状态一致。
- ARCA 系多实例共享 NAS 目录的写入，沿用 skills-pool 的 temp + 原子 rename
  模式（quarantine/probe 机制已有先例）。

## 4. 源引用与 fetch

### 4.1 统一由平台侧 fetch

manifest 条目的内容来源三选一：`source`（URL）、`content`（内联）、平台注册
项引用（如 MCP 的 `server_code`）。**URL 源统一由平台在 apply 时 fetch**，
对两个引擎家族语义一致：

- 对 teclaw：fetch 后物化进 OSS store，artifact 里只出现它今天已认识的
  `{store, path}` 引用——不给 teclaw 容器加任何新的出网要求。
- 对 ARCA 系：fetch 后经现有实体服务落库、走现有交付。

推论（列入业务方前提确认清单）：**manifest 的源必须平台侧可达**。只有沙箱
网络可达的源属于 script 的领域（ARCA-only）。这是刻意选择：统一的
fetcher、统一的 SSRF 防护、统一的新鲜度语义，换一条明确的能力边界。

### 4.2 新鲜度与钉扎

承诺一句话：「源在每个 apply 点重新 fetch——bot 每次重新拉起，拿到的是当时
源上的最新版」。需要严格可复现（同一 manifest 永远同一状态）的用户，可在
条目上加 `digest`（sha256）钉住；digest 不匹配按 fetch 失败处理。

git 源（schema §2.2）的对应物是 `ref`：写 branch 追最新、写 tag/SHA 钉版；
收敛以每个 apply 点解析出的 commit SHA 为准，`ref` 与 SHA 双双记入 apply
report。tag 被重打即声明含义变化，下次 apply 收敛到新内容——与「声明获胜」
一致；要绝对不可变直接写 SHA。

### 4.3 失败策略

条目级 `on_fetch_failure`，三个值：

| 值 | 行为 |
| --- | --- |
| `keep_last`（默认） | 沿用上一次成功物化的版本；从未成功过则记 `skipped`。源站抖动不影响 bot 拉起 |
| `skip` | 记 `skipped`，继续后续条目 |
| `fail` | 中止本次 apply，剩余条目记 `skipped`，apply 结果 `FAILED` |

**apply 失败与就绪门控**：v1 与 #935 script 的语义保持一致——用户配置应用失败
**不阻断** bot 就绪（平台自身 bootstrap 失败照旧 FAILED），结果完整记录在
apply report。理由：同一产品功能在两类引擎上失败表现必须一致，业务才解释得
通。teclaw 的 publish-poll（PENDING → ACTIVE/FAILED）具备做强门控（配置应用失败
= 不就绪）的条件，「strict 模式」列为 v2 讨论项，且若做必须两个家族同步做。

### 4.4 Guarded fetcher（SSRF 防护）

平台代用户 fetch 任意 URL 是标准 SSRF 攻击面，fetcher 必须收口为一个带防护
的组件（引擎仓库 d36cb39「guarded temporary URL downloads」同类先例）：

- scheme 仅 `https`（`http` 仅限部署级显式允许的内网源）；
- 解析后目标 IP 拒绝环回 / 链路本地 / 元数据段 / RFC1918（除非部署级
  allowlist），重定向逐跳同样校验；
- 条目级大小上限、总量上限、超时、限并发；
- 响应只按字节流处理，永不执行。

### 4.5 私有源鉴权：凭证引用（credential reference）

fetch 是平台发出的普通 HTTPS GET，所以私有源鉴权的问题是「平台以什么身份
去取」。原则先立住：**secret 永远不出现在 manifest / script / source URL
里**——manifest 会被 GET 原样读回、出现在变更审计里；script 的下发链路
日志可见（#935「体内无密」结论维持不变）。鉴权通过**引用**完成：

1. **凭证是租户级命名对象**，独立于任何一个 bot 存储（同一个 token 服务
   整批 bot，正是要消灭按 bot 重复配置）。经独立 API 写入（见 §6），
   字段：`header_name`（如 `Authorization`）、`secret`（完整头值，如
   `Bearer eyJ…`）、`allowed_prefixes`（见 3）。**写后不可读回**：GET 只
   返回掩码元数据（`has_secret`、`header_name`、`allowed_prefixes`、
   `updated_at`），与现有 MCP 统一配置「`api_key` 存储、读时掩码、用时
   注入」是同一生命周期模式（`openapi_v1/mcp/router.py` 现状）——不是
   平台新增的能力类别。
2. **manifest 条目以名字引用**：`auth: <credential-name>`（或声明在命名源
   上），fetch 时平台把该凭证注入为请求头。
3. **凭证绑定 URL 前缀**：`allowed_prefixes` 在创建时声明、**必填**，
   fetch 目标不在名单内 → 该条目直接 `failed`（配置错误，明确报出，不是
   静默不带凭证）。这防的是持 manifest 编辑权的人把 `source` 指向别处
   套取 token。**跨前缀重定向直接失败**——不是剥离凭证后继续，失败更不易
   被误用。前缀按**路径段边界**匹配（`…/team/content` 不得匹配
   `…/team/content-secret`）。

   **一个端点、一个 body schema**：body 的判别键 `type` 是**认证机制**，
   不是存储类型——`git`/`oss`/`url` 属于 `source`，凭证不关心（git 源调
   托管服务 HTTP API、URL 源发普通 GET，注入动作相同）。v1 只实现
   `type: header`（`header_name` + `secret`）；预留 `oss_aksk`
   （`access_key_id` + `access_key_secret`，AK/SK 每请求现场签名，装不进
   header 形状）与 `basic`，将来加进同一端点而不必新开接口。前缀这一个字段同时
   覆盖两种收敛：git 的仓库白名单与对象存储的桶前缀，本质都是「某 origin
   下的路径前缀」。二者都是单 origin 承载大量互不相关内容，故前缀必填、
   不设「整 origin」默认；要覆盖整个 origin 需显式写 `https://host/`。
4. **生命周期**：轮换 = 重新 PUT 同名凭证，下一个 apply 点自然用新值
   （不触发 apply，惰性口径一致）；删除仍被引用的凭证 → 引用条目 apply
   时 `failed`（「credential X 不存在」）。apply report 只记凭证**名**，
   永不记值。
5. **存储与主密钥托管**：**必须可逆加密而非哈希**——密码存储用哈希（单向、
   只需验证），这里的用途是代表用户**出示** token，必须能还原明文。复用
   仓库既有实现，不新建加密方案：`utils/secret_utils.py` 的
   `symmetric_encrypt/decrypt`（AES-GCM）作原语，
   `core/bot_management/token_vault.py` 的 `TokenVault` 作落库封装（其既有
   用途正是「外部平台 token 落库前加密」，与具体平台无关），主密钥经
   `SecretResolver` 从密钥库（企业环境的 Mist registry）解析。落库形态
   `enc:v1:<密文>`（既有前缀设计，读端可区分存量明文，零迁移；换算法可升
   `v2`）。解密只发生在 fetch 前的内存中，用完即弃。

   **本场景必须新增一道守卫**：`TokenVault` 在 master_key 为空时明文直落
   （为本地联调，与 `outbound_rules` 单 box 同形）——这对 源凭证
   在生产环境不可接受。生产 profile 下解析不到主密钥必须**拒绝写入凭证**
   （fail closed），否则一次密钥库配置疏忽就会让全租户 token 明文躺在 DB 里。
6. **引擎面为零**：fetch 全在平台侧完成，凭证不下发容器、不进 artifact
   （`StoreRef` 契约本就是 "location only — never credentials"）。MCP
   凭证的 compose 时内联是既有契约、与本机制无关，照现状不变。

token 选型（与 `allowed_prefixes` 叠加的纵深防御）：**首选仓库级/桶级只读
token**（类 GitLab 的 Project/Deploy Token，天生单仓库有效）；托管服务不
支持时用**机器人账号 token**（以成员关系收权）；**不用个人 PAT**——权限面
是个人全量可见仓库，且生命周期绑定个人。托管服务能力确认见 O11。

无鉴权基线仍然成立：公开源、网络 ACL 自保护的源、签名 URL（有过期问题，
只适合一次性场景）都不需要凭证引用。v1 注入方式仅支持请求头；query 参数
型、mTLS 列入 v2 评估（开放问题 O8）。

**MCP 凭证永不进 manifest**：manifest 只写 `server_code` 引用平台 MCP
注册表；凭证照现状由平台持有、compose 时按 `McpServerRef` 现有机制内联。

## 5. 能力模型与版本化

### 5.1 支持判定：二维、从 bot 记录可答、fail closed

支持与否是 `(engine_type, provider/platform)` 二维函数，且**必须只凭 bot
记录回答、不碰活容器**（#935 `_support.py` 的教训：读活绑定引入第三种
「查不到」状态，把无关抖动变成对 bot 的误判）。读写两侧用同一判定函数，
否则 GET 宣称支持而 PUT 拒绝。未知引擎 fail closed。

`GET …/config-manifest/capabilities` 返回该 bot 的逐类别支持表，业务可先探再写。

判定结果同时堵上 #935 已知的静默坑：ARCA-direct 遗留 bot、LOCAL/singlebox
的 script 判定为不支持（而非静默不执行）；manifest 因为是平台侧 apply，
在这些形态上反而可以支持（见 `engine-requirements.zh-CN.md` 矩阵）。

### 5.2 版本

- manifest 文档带 `schema_version`（v1 = 1），未知版本拒绝写入。
- teclaw 的 `BotConfigArtifact.schema_version` **不因本设计变化**——编译
  产物落在现有词汇表内，这是刻意的设计约束。
- 平台⇄引擎若新增契约（如 v2 的条目级结果上报），按
  `skills-pool-mapping-v2` 先例独立版本化、probe 门控。

## 6. API 面（草案）

| 方法与路径 | 语义 |
| --- | --- |
| `GET /openapi/v1/bots/{bot_id}/config-manifest` | 读整份配置清单文档（manifest + script） |
| `PUT /openapi/v1/bots/{bot_id}/config-manifest` | 整体替换；校验 schema、逐类别能力、限额；对不支持的部分整体拒绝（400/422 带逐条原因），不部分写入 |
| `DELETE /openapi/v1/bots/{bot_id}/config-manifest` | 清除。已由 manifest 落成的实体**保留但摘除 managed 标记**（变回手工实体），不级联删除——删除声明 ≠ 删除资产 |
| `GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities` | 该 bot 的逐类别支持表 |
| `POST /openapi/v1/bots/{bot_id}/config-manifest/apply` | 显式 apply（可带 `dry_run=true` 返回计划不执行） |
| `GET /openapi/v1/bots/{bot_id}/config-manifest/last-apply` | 最近一次 apply report |
| `PUT /openapi/v1/bots/source-credentials/{name}` | 写入/轮换租户级命名凭证：单一 body schema，判别键 `type`（v1 仅 `header`：`header_name` + `secret`）+ `allowed_prefixes`，§4.5。**一个端点覆盖 git / URL / OSS**。租户级路径：凭证不属于单个 bot |
| `GET /openapi/v1/bots/source-credentials[/{name}]` | 列表 / 单个，仅掩码元数据，**永不返回 secret** |
| `DELETE /openapi/v1/bots/source-credentials/{name}` | 删除；仍被引用时引用条目在下次 apply 记 `failed` |

兼容性：既有 `GET/PUT/DELETE /openapi/v1/bots/{bot_id}/startup-script`
（#935）保留，行为不变。**不**成为配置清单文档 `script` 部分的别名视图——W8 评审
（inclusionAI/Avernet#1836）决定清单是启动脚本不感知的上层：老端点只读写自己的
那一行，清单声明的 `script` 在 apply 时物化进同一行，后者覆盖前者。鉴权沿用
`_GRANT_CHECKED` + `ADMISSION` 模式。

## 7. 可观测性

apply 在平台侧执行，天然产出结构化记录（#935 的 `last-start` 因「scaled bot
无法回答是哪次启动」被砍；平台侧 apply 没有这个歧义）：

```json
{
  "apply_id": "…", "bot_id": "…", "trigger": "create|republish|restart|explicit",
  "started_at": "…", "finished_at": "…", "result": "SUCCEEDED|PARTIAL|FAILED",
  "sources": [
    {"name": "content", "ref": "v1.2.0", "resolved_sha": "9c1f4ae…"}
  ],
  "entries": [
    {"category": "skills", "name": "reviewer",
     "action": "created|updated|unchanged|skipped|failed",
     "from": "content", "source_digest": "sha256:…", "error": null}
  ]
}
```

命名源的解析结果记在顶层 `sources`（声明的 `ref` + 解析出的
`resolved_sha`）——「这批 bot 线上跑的是哪一版内容」由此可查；条目层记
`from`（来自哪个源）或 `source_digest`（URL 源）。

经 `GET …/config-manifest/last-apply` 暴露。script 的输出维持现状：容器内
`/home/admin/logs/startup_script.log`。

## 8. 存储与租户

沿用 `bot_startup_script` 模块验证过的机制：bot 级一行、
`(avernet_tenant, script_key)` 唯一键（`script_key = sha256(env, entity_id,
bot_id)`，规避 InnoDB 3072 字节索引上限）、租户守卫注册。实现形态（新
`core/bot_config_manifest` 模块吸收 `bot_startup_script`，或并列）留到实现
planning 决定；模块边界按 `context-boundary-format.md` 出 README。

## 9. 分期

| 期 | 内容 |
| --- | --- |
| **v1** | manifest 五类（mcp / resources / skills / engine_config / identity；resources 含**目录条目**——归档 + `strip_components` 展开，schema §3.2）+ script 归编到配置清单文档；source 支持 URL 与 **git 引用**两种形态（tag/branch/SHA 版本化，schema §2.2；凭证统一形状，`allowed_prefixes` 必填），**命名源 `sources`/`from`** 让一次 `ref` 变更原子地升级整套配置（schema §2.3）；平台侧 apply + guarded fetcher；租户级凭证引用（§4.5，仅请求头注入；AES-GCM 落库 + 密钥库主密钥）；能力表；apply report；teclaw 经 artifact 组装生效 |
| **cli_tools（schema 已定稿，排期后置）** | 给模型调用的命令行工具（schema §3.7）：**一个条目 = 一个命令 = 一个自包含可执行文件**（压缩包为传输形态，`subpath` 取出其中一个文件）、digest 强制、平台工具目录 + PATH 注入；ARCA 系先行（A2），teclaw 按 `teclaw-cli-contract.zh-CN.md` 实现（artifact 新增 `cli_tools`，含平台计算的 `md5`，`schema_version` 4 → 5）。按业务优先级排期 |
| **v2 候选** | 条目级结果上报（teclaw 唯一可能的契约增量）；strict 就绪门控；`apply_once`；skill-center 引用源（`center://uuid@version`）；目录源的更多传输形态（索引文件 / 对象存储前缀——「文件夹语义」需要带目录枚举能力的协议；git 与归档已进 v1）；engine plugin 类目（**注册表引用**模式，照 MCP 模子而非任意 URL——插件在引擎进程内自动执行，供应链敏感度最高；前置确认见 O10）；容器内 op CLI（服务 script 用户体验：`install-skill` 等意图层命令，ARCA 系实现）；凭证注入的扩展形态（query 参数 / mTLS，O8）；模板级 manifest（一份声明应用于多个 bot）；**manifest 自身托管于 git**（§9.1） |

### 9.1 v2 方向：manifest 自身托管于 git

顺着「配置内容都是文本表达」推到底，**配置声明本身也是文本**，也可以活在
业务的内容仓库里。平台侧 bot 上存的退化为一个**指针**——
`{git, ref, credential}`，`manifest.yaml` 从仓库根读取。这是 Flux/ArgoCD
的模式，收益有三：

- **声明与内容在同一个 tag 下原子一致**——不会出现「manifest 升了 ref、
  内容没跟上」的错位（v1 的命名源已在单份文档内解决了这个问题，git 托管
  把它扩展到「文档自己也跟着走」）；
- **配置变更走 PR 评审**，审计天然；
- **顺手解掉 O6**：一个仓库 + 一个 tag 指给整个 bot 舰队，就是模板级
  manifest。

代价必须一并记下：写入路径要重做（open API 从「PUT 整份文档」变为
「PUT 指针」）；**能力校验从 PUT 时挪到 apply 时**——「teclaw 写 script
当场拒绝」这类 fail-fast 会弱化为 apply report 里的错误，与 §5.1 的
fail-closed 口径需要重新对齐。因此 v1 不做，待业务用起来、验证「整仓库
一个 tag」确实是主流形态后再评估。

## 10. 实现注意（backend 内部，不涉及引擎侧改动）

1. **ARCA 系物化时序**：apply 在 compose 前完成实体落库；物理交付依赖现有
   机制（NAS 持久 + 对活容器的 push）。新建容器场景需确认「manifest 实体
   先于 script 阶段就位」这一顺序保证的落点——候选：交付走 NAS 路径（重建
   后即可见）或启动链早段拉取。实现 planning 时定，作为对 §3.4 顺序保证的
   验收项。
2. **与 skills-pool reconcile 的边界**：manifest skills 经正规实体服务落库
   后 reconcile 自然认识；实现评审确认 quarantine 清理逻辑对 managed 实体
   的处理路径有测试覆盖。
3. **apply 与 compose 的失败隔离**：fetch/apply 失败按 §4.3 策略处理，不得
   把源站抖动放大为 compose 失败（`keep_last` 默认值是这道保险）。
4. **`_get_start_cmd` 不变式**：无 script 的 bot 启动命令必须与 #935 之前
   byte-identical（既有测试断言保留）；manifest 不进启动命令。
5. **git 源的实现口径**：优先走托管服务的 HTTP API（refs 解析 + 按
   ref/子目录取归档），把 git 源编译为「一次 HTTPS 归档拉取 + 解包」，
   复用 guarded fetcher 与归档管线；不在后端进程内跑 `git clone`。API
   能力（O11）不满足时再评估 clone 方案。
