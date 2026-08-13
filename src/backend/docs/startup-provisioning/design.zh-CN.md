# Startup Provisioning 设计

> 状态：DRAFT（对接讨论稿）。术语与文档地图见 `README.zh-CN.md`。

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

### 2.1 置备意图 = manifest + script

一个 bot 的启动置备意图存为**一份 bot 级文档**，含两个部分：

| 部分 | 性质 | 表达能力 | 引擎支持 |
| --- | --- | --- | --- |
| `manifest` | 声明式 | 「要什么、从哪来」：MCP、workspace 资源、local skills、engine config、identity | **所有引擎**（含 teclaw） |
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
  引擎对接面缩到最小。

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
配置好，而启动置备干的正是配置引擎；④ 每次启动付 token 成本与 agent 时延
（现有 user stage 预算 300s）；⑤ 审计与回放困难。但两者不冲突：确定性置备
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
| `POST …/provisioning/apply`（显式） | ✅ | 用户要「立即生效」时用。对 teclaw = 重组一份新版本 artifact、推**一次**——单位仍是整份文档，不退化为调用风暴 |

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
  显式写进对接文档（见 `engine-alignment.zh-CN.md` 确认项 T2）。

### 3.4 类别间顺序与并发

- 单次 apply 内固定顺序：`engine_config → identity → resources → skills →
  mcp`。script（若有）在全部交付完成后、启动链末端执行——**script 可以依赖
  manifest 声明的实体已经就位**。
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

推论（必须写进业务 checklist）：**manifest 的源必须平台侧可达**。只有沙箱
网络可达的源属于 script 的领域（ARCA-only）。这是刻意选择：统一的
fetcher、统一的 SSRF 防护、统一的新鲜度语义，换一条明确的能力边界。

### 4.2 新鲜度与钉扎

承诺一句话：「源在每个 apply 点重新 fetch——bot 每次重新拉起，拿到的是当时
源上的最新版」。需要严格可复现（同一 manifest 永远同一状态）的用户，可在
条目上加 `digest`（sha256）钉住；digest 不匹配按 fetch 失败处理。

### 4.3 失败策略

条目级 `on_fetch_failure`，三个值：

| 值 | 行为 |
| --- | --- |
| `keep_last`（默认） | 沿用上一次成功物化的版本；从未成功过则记 `skipped`。源站抖动不影响 bot 拉起 |
| `skip` | 记 `skipped`，继续后续条目 |
| `fail` | 中止本次 apply，剩余条目记 `skipped`，apply 结果 `FAILED` |

**apply 失败与就绪门控**：v1 与 #935 script 的语义保持一致——用户置备失败
**不阻断** bot 就绪（平台自身 bootstrap 失败照旧 FAILED），结果完整记录在
apply report。理由：同一产品功能在两类引擎上失败表现必须一致，业务才解释得
通。teclaw 的 publish-poll（PENDING → ACTIVE/FAILED）具备做强门控（置备失败
= 不就绪）的条件，「strict 模式」列为 v2 讨论项，且若做必须两个家族同步做。

### 4.4 Guarded fetcher（SSRF 防护）

平台代用户 fetch 任意 URL 是标准 SSRF 攻击面，fetcher 必须收口为一个带防护
的组件（引擎仓库 d36cb39「guarded temporary URL downloads」同类先例）：

- scheme 仅 `https`（`http` 仅限部署级显式允许的内网源）；
- 解析后目标 IP 拒绝环回 / 链路本地 / 元数据段 / RFC1918（除非部署级
  allowlist），重定向逐跳同样校验；
- 条目级大小上限、总量上限、超时、限并发；
- 响应只按字节流处理，永不执行。

### 4.5 凭证边界

- **manifest 静态存储的保密级别 = 既有 bot 配置（DB）**，高于 script
  （script 因下发链路日志可见，维持 #935「体内无密」结论不变）。
- 但 **source URL 中不得携带长期凭证**：v1 的口径是用户源站以网络 ACL 或
  签名 URL 自保护。签名 URL 有过期问题，只适合一次性场景，不适合「每个
  apply 点重取」，文档如实告知。对私有源的正经凭证通道（引用平台凭证存储）
  是独立的后续设计，不搭本期便车。
- **MCP 凭证永不进 manifest**：manifest 只写 `server_code` 引用平台 MCP
  注册表；凭证照现状由平台持有、compose 时按 `McpServerRef` 现有机制内联。

## 5. 能力模型与版本化

### 5.1 支持判定：二维、从 bot 记录可答、fail closed

支持与否是 `(engine_type, provider/platform)` 二维函数，且**必须只凭 bot
记录回答、不碰活容器**（#935 `_support.py` 的教训：读活绑定引入第三种
「查不到」状态，把无关抖动变成对 bot 的误判）。读写两侧用同一判定函数，
否则 GET 宣称支持而 PUT 拒绝。未知引擎 fail closed。

`GET …/provisioning/capabilities` 返回该 bot 的逐类别支持表，业务可先探再写。

判定结果同时堵上 #935 已知的静默坑：ARCA-direct 遗留 bot、LOCAL/singlebox
的 script 判定为不支持（而非静默不执行）；manifest 因为是平台侧 apply，
在这些形态上反而可以支持（见 `engine-alignment.zh-CN.md` 矩阵）。

### 5.2 版本

- manifest 文档带 `schema_version`（v1 = 1），未知版本拒绝写入。
- teclaw 的 `BotConfigArtifact.schema_version` **不因本设计变化**——编译
  产物落在现有词汇表内，这是刻意的设计约束。
- 平台⇄引擎若新增契约（如 v2 的条目级结果上报），按
  `skills-pool-mapping-v2` 先例独立版本化、probe 门控。

## 6. API 面（草案）

| 方法与路径 | 语义 |
| --- | --- |
| `GET /openapi/v1/bots/{bot_id}/provisioning` | 读整份置备文档（manifest + script） |
| `PUT /openapi/v1/bots/{bot_id}/provisioning` | 整体替换；校验 schema、逐类别能力、限额；对不支持的部分整体拒绝（400/422 带逐条原因），不部分写入 |
| `DELETE /openapi/v1/bots/{bot_id}/provisioning` | 清除。已由 manifest 落成的实体**保留但摘除 managed 标记**（变回手工实体），不级联删除——删除声明 ≠ 删除资产 |
| `GET /openapi/v1/bots/{bot_id}/provisioning/capabilities` | 该 bot 的逐类别支持表 |
| `POST /openapi/v1/bots/{bot_id}/provisioning/apply` | 显式 apply（可带 `dry_run=true` 返回计划不执行） |
| `GET /openapi/v1/bots/{bot_id}/provisioning/last-apply` | 最近一次 apply report |

兼容性：既有 `GET/PUT/DELETE /openapi/v1/bots/{bot_id}/startup-script`
（#935）保留，成为置备文档 `script` 部分的别名视图（write-through），行为
不变。鉴权沿用 `_GRANT_CHECKED` + `ADMISSION` 模式。

## 7. 可观测性

apply 在平台侧执行，天然产出结构化记录（#935 的 `last-start` 因「scaled bot
无法回答是哪次启动」被砍；平台侧 apply 没有这个歧义）：

```json
{
  "apply_id": "…", "bot_id": "…", "trigger": "create|republish|restart|explicit",
  "started_at": "…", "finished_at": "…", "result": "SUCCEEDED|PARTIAL|FAILED",
  "entries": [
    {"category": "skills", "name": "reviewer",
     "action": "created|updated|unchanged|skipped|failed",
     "source_digest": "sha256:…", "error": null}
  ]
}
```

经 `GET …/provisioning/last-apply` 暴露。script 的输出维持现状：容器内
`/home/admin/logs/startup_script.log`。

## 8. 存储与租户

沿用 `bot_startup_script` 模块验证过的机制：bot 级一行、
`(avernet_tenant, script_key)` 唯一键（`script_key = sha256(env, entity_id,
bot_id)`，规避 InnoDB 3072 字节索引上限）、租户守卫注册。实现形态（新
`core/bot_provisioning` 模块吸收 `bot_startup_script`，或并列）留到实现
planning 决定；模块边界按 `context-boundary-format.md` 出 README。

## 9. 分期

| 期 | 内容 |
| --- | --- |
| **v1** | manifest 五类（mcp / resources / skills / engine_config / identity）+ script 归编到置备文档；平台侧 apply + guarded fetcher；能力表；apply report；teclaw 经 artifact 组装生效 |
| **v2 候选** | 条目级结果上报（teclaw 唯一可能的契约增量）；strict 就绪门控；`apply_once`；skill-center 引用源（`center://uuid@version`）；容器内 op CLI（服务 script 用户体验：`install-skill` 等意图层命令，ARCA 系实现）；私有源凭证通道；模板级 manifest（一份声明应用于多个 bot） |

## 10. 实现注意（backend 内部，不影响引擎对接）

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
