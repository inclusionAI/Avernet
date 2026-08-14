# 引擎侧要求：工作量、能力矩阵与待确认清单

> 状态：DRAFT（讨论稿）。内容：每个引擎/形态要做什么、要确认什么、以及
> 尚未定案的问题。设计论证见 `design.zh-CN.md`。

## 1. 先说结论：各方工作量

| 团队 | v1 工作量 | 说明 |
| --- | --- | --- |
| backend（平台） | **主体**：置备文档存储 + API、平台侧 apply、guarded fetcher、能力表、apply report | 全部在平台侧 |
| teclaw | **零改动**（3 项确认，见 §3） | artifact schema 不动、组装管线不动、不加出网要求 |
| ARCA 系引擎（openclaw / claude_code / aicoding / hermes / moltis） | **零改动**（1 项确认，见 §4） | 声明式走平台实体 + 现有交付；script 走 #935 现状 |
| BaaS | **零改动** | 启动链、hook 派发均不变 |
| 业务方 | 回答 §6 的确认清单 | 决定 v1 边界是否够用 |

本设计的刻意目标就是把新增集中在平台侧：manifest 编译产物落在各引擎**今天
已经消费的通道**里（teclaw：`BotConfigArtifact` 现有词汇表；ARCA 系：现有
push / NAS 交付），引擎看到的输入形状与今天用户手工调用 TC Open API 上传
skill / identity 之后完全相同。

## 2. 能力矩阵

按 `(engine_type, provider/platform)` 二维判定（design §5.1），从 bot 记录
可答、fail closed：

| 形态 | manifest（五类） | script | 备注 |
| --- | --- | --- | --- |
| openclaw / aicoding / hermes / moltis @ ARCA/BaaS（ARCA, SIGMA, POOLAB, DOCKER） | ✅ 全部 | ✅ | 现状主流形态 |
| claude_code @ ARCA/BaaS | ✅（identity 仅 `CLAUDE.md`） | ✅ | identity 合法集按引擎校验 |
| teclaw @ TECLAW | ✅ 全部（engine_config 见确认项 T3） | ❌ 写入拒绝 | 经 artifact 组装生效 |
| desktop | ✅（平台侧 apply 可行，交付按其现状通道） | ❌ 写入拒绝（#935 现状口径） | 需 desktop owner 确认交付路径，见 O2 |
| LOCAL / singlebox | ✅ | ❌ 写入拒绝 | #935 的静默坑（hook 不派发）在新判定中变为显式拒绝 |
| ARCA-direct 遗留 bot | ✅ | ❌ 写入拒绝 | 同上，静默不执行 → 显式拒绝 |
| 未知引擎 | ❌ | ❌ | fail closed |

对比 #935：script 的支持面**没有扩大**，但判定从「静默不执行」收紧为「写入
时拒绝」；manifest 因为是平台侧 apply，支持面覆盖到了 script 到不了的形态。

## 3. teclaw：3 项确认（T1–T3）

零改动的论证：manifest apply 落成平台实体 → `TeclawProvisionService` 组装
artifact 时照旧读平台状态 → 引擎拉到的仍是现有 `schema_version` 的
`BotConfigArtifact`，URL 源已由平台物化进 OSS store，出现在 artifact 里的
只是它今天已认识的 `{store, path}`。bot 拿到的**第一份** artifact 即包含
manifest 结果（apply 先于组装），不存在「起来之后逐个补打」。

需 teclaw 团队确认的三件事：

- **T1（就绪时序）**：引擎在向 publish-poll 报告就绪**之前**完成 artifact
  应用（skills / identity / resources / mcp 落地）。我们理解现状即如此，
  请确认——这是「配置先于就绪」语义在 teclaw 侧的落点。
- **T2（收敛应用）**：同一份 artifact 重复投递（update、restart 重拉）收敛
  到同一状态、无副作用累积；条目内容变化时（同 name 的 skill 指到新的
  store 对象）旧内容被替换而非并存。我们理解现有整包重投模型已隐含此语义，
  请确认为显式契约。
- **T3（engine config 的创建时序）**：`engine_config` 类别**不落 artifact
  字段**——它走既有通道：provider-blind 的 engine-config 服务把逻辑路径
  `config/teclaw.json` 的 JSON 文档经贵侧引擎的 `/api/v1/file/upload`
  逐文件写入（`DeviceFilesystemDispatcher.engine_config_path` +
  `TeclawDeviceFileSystem` 现状——今天的
  `PUT /openapi/v1/bots/{bot_id}/engine-config` 已经这样工作）。
  `engine_overrides` 保持不用。需确认的是**新建 bot 场景的时序**：
  ① 创建时容器尚未就绪，apply 产出的 `config/teclaw.json` 如何到达首个
  实例——初始文件集是否会包含它，还是 apply 需在 ACTIVE 后经逐文件通道
  补写；② 引擎何时读取该文件（仅启动读一次，还是会重读）——这决定
  ACTIVE 后补写是否需要重启才生效。

v2 才可能涉及的唯一契约增量：**条目级应用结果上报**（哪个 skill 装成功了）。
v1 先以 publish-poll 的整体成败 + 平台侧 apply report（fetch/物化环节的
条目级结果）覆盖，不要求 teclaw 提供。

## 4. ARCA 系引擎：1 项确认（A1）

声明式部分零改动：apply 落成的实体与用户今天手工调 TC Open API 的结果无
差别，交付通道现状不变。script 部分即 #935 现状。

- **A1（交付时序）**：新建容器场景下，「manifest 实体的物理交付完成」需要
  先于「script 阶段执行」（design §3.4 的顺序保证——脚本可以假定声明的
  skill / identity 已就位）。identity / skills 的交付若依赖 NAS 持久化，
  重建后天然满足；若存在依赖「对活容器 push」的实体类别，需与 backend 一起
  确认其在启动链中的时点（backend 实现注意项 design §10.1，由 backend
  先行内部确认）。

可选（v2，非本期承诺）：容器内 op CLI（`install-skill` 等意图层命令，封装
对本机引擎适配器 API 的调用 + 就绪等待 + 重试），提升 script 用户的体验。
届时按 `startup-ops` 独立契约版本化，probe 门控。

## 5. 开放问题（定稿前需要拍板）

| # | 问题 | 倾向 | 决策人 |
| --- | --- | --- | --- |
| O1 | teclaw engine config 的创建时序（=T3：首实例如何拿到 `config/teclaw.json`、引擎读取时机） | 走既有 `config/teclaw.json` 文件通道；`engine_overrides` 不启用 | teclaw + backend |
| O2 | desktop 是否纳入 v1 的 manifest 支持面 | 纳入（平台侧 apply 无额外成本），交付路径由 desktop owner 确认 | desktop owner |
| O3 | 显式 `POST …/apply` 是否进 v1 | 进（业务「立即生效」诉求大概率存在；teclaw 侧 = 一次 artifact 重组下发，无新机制） | backend + 业务 |
| O4 | 限额数值（manifest-schema §5） | 按建议值起步 | backend |
| O5 | strict 就绪门控（置备失败 = 不就绪）是否 v2 做 | v2 再议，两家族必须同步 | 平台 + 两引擎家族 |
| O6 | 模板级 manifest（一份声明 → 多个 bot） | v2；v1 仅 bot 级 | 业务 |
| O7 | `center://` skill-center 引用源 | v2 | backend |
| O8 | 凭证注入方式是否需要请求头之外的形态（query 参数 token / mTLS） | v1 仅请求头；有真实业务源依赖再评估 | backend + 业务 |

## 6. 业务方前提确认清单

1. **源的可达性**：你们的内容源（skill zip、md 所在服务）**平台侧可达吗**？
   manifest 的源统一由平台 fetch（design §4.1）；只有沙箱网络可达的源只能
   走 script（则 teclaw 上不可用）。
2. **命令式残留比例**：现有/预期的启动脚本里，除「取内容 → 装上」外还有
   多少真逻辑（条件、转换、动态决策）？这决定 script 的实际依赖面，以及
   「teclaw 不支持 script」影响多少用户。
3. **「teclaw 要支持」的确切含义**：要的是这些**场景**（装 skill、传
   identity 等，manifest 全覆盖），还是字面上「用户脚本在 teclaw 容器里
   跑」（v1 明确不做，需 teclaw 侧出现执行通道后另行评估）？
4. **生效时机**：「下次重建生效」（默认惰性）够不够？「立即生效」的显式
   apply（O3）是否 v1 必须？
5. **凭证现实**：内容源目前用什么鉴权？v1 的凭证引用（design §4.5）仅
   支持请求头注入（如 `Authorization: Bearer …`）；若你们的源依赖 query
   参数 token 或 mTLS，请提出（对应开放问题 O8）。无鉴权基线（公开源 /
   网络 ACL / 签名 URL）不受影响。
6. **更新频率与体量**：源内容多大、多久变一次？用于校准限额（O4）与
   fetch 预算。
