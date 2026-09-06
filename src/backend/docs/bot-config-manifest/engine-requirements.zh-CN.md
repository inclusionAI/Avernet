# 引擎侧要求：工作量、能力矩阵与待确认清单

> 状态：DRAFT（讨论稿），**§2 能力矩阵与 §3 的 T1/T2/T4 已按 2026-08-31 的
> teclaw 确认更新**。内容：每个引擎/形态要做什么、要确认什么、以及尚未定案的
> 问题。设计论证见 `design.zh-CN.md`。
>
> **本文早于 `engine-convergence-contract.zh-CN.md` 与 `teclaw-cli-contract.zh-CN.md`；
> 两者与本文冲突时以两者为准。**

## 1. 先说结论：各方工作量

| 团队 | v1 工作量 | 说明 |
| --- | --- | --- |
| backend（平台） | **主体**：配置清单文档存储 + API、平台侧 apply、guarded fetcher、能力表、apply report | 全部在平台侧 |
| teclaw | **仅 `cli_tools` 一项新增**（T1/T2/T4 已确认；T3 移出第一期，T5 可选） | 组装管线不动、不加出网要求；artifact 新增一个 `cli_tools` 字段，**`schema_version` 不升版** |
| ARCA 系引擎（openclaw / claude_code / aicoding / hermes / moltis） | 声明式**零改动**；`cli_tools` 需新增四个按命令名寻址的端点（A2：单个装/删/列 + 整体替换） | 声明式走平台实体 + 现有交付；script 走 #935 现状；cli_tools 的落点与可执行位由引擎在 `install` 内完成 |
| BaaS | **零改动** | 启动链、hook 派发均不变 |
| 业务方 | 回答 §6 的确认清单 | 决定 v1 边界是否够用 |

本设计的刻意目标就是把新增集中在平台侧：manifest 编译产物落在各引擎**今天
已经消费的通道**里（teclaw：`BotConfigArtifact` 现有词汇表；ARCA 系：现有
push / NAS 交付），引擎看到的输入形状与今天用户手工调用 TC Open API 上传
skill / identity 之后完全相同。

## 2. 能力矩阵

按 `(engine_type, provider/platform)` 二维判定（design §5.1），从 bot 记录
可答、fail closed：

| 形态 | manifest（五类） | cli_tools | script | 备注 |
| --- | --- | --- | --- | --- |
| openclaw / aicoding / hermes / moltis @ ARCA/BaaS（ARCA, SIGMA, POOLAB, DOCKER） | ✅ 全部 | ✅（W9 已交付；引擎端点见 A2） | ✅ | 现状主流形态 |
| claude_code @ ARCA/BaaS | ✅（identity 仅 `CLAUDE.md`） | ✅（同上） | ✅ | identity 合法集按引擎校验 |
| teclaw @ TECLAW | ✅ 全部（engine_config 移出第一期） | ✅ **已确认**（2026-08-31；契约见 `teclaw-cli-contract.zh-CN.md`） | ❌ 写入拒绝（teclaw owner 再次确认，2026-08-31） | 经 artifact 组装生效 |
| desktop | ✅（平台侧 apply 可行，交付按其现状通道） | ❌ 写入拒绝（全局 desktop 拒绝优先于逐类目判定） | ❌ 写入拒绝（#935 现状口径） | 需 desktop owner 确认交付路径，见 O2 |
| LOCAL / singlebox | ✅ | 待定 | ❌ 写入拒绝 | #935 的静默坑（hook 不派发）在新判定中变为显式拒绝 |
| ARCA-direct 遗留 bot | ✅ | 待定 | ❌ 写入拒绝 | 同上，静默不执行 → 显式拒绝 |
| 未知引擎 | ❌ | ❌ | ❌ | fail closed |

对比 #935：script 的支持面**没有扩大**，但判定从「静默不执行」收紧为「写入
时拒绝」；manifest 因为是平台侧 apply，支持面覆盖到了 script 到不了的形态。

## 3. teclaw：确认项（T1–T5）

零改动的论证：manifest apply 落成平台实体 → `TeclawProvisionService` 组装
artifact 时照旧读平台状态 → 引擎拉到的仍是现有 `schema_version` 的
`BotConfigArtifact`，URL 源已由平台物化进 OSS store，出现在 artifact 里的
只是它今天已认识的 `{store, path}`。bot 拿到的**第一份** artifact 即包含
manifest 结果（apply 先于组装），不存在「起来之后逐个补打」。

需 teclaw 团队确认的事项（T1–T3 为 v1 必答；T4 随 `cli_tools` 排期；T5
为可选优化）：

- **T1（就绪时序）—— ✅ 已确认**：引擎在向 publish-poll 报告就绪**之前**完成
  artifact 应用（skills / identity / resources / mcp 落地）。这是「配置先于
  就绪」语义在 teclaw 侧的落点，对应
  `engine-convergence-contract.zh-CN.md` A4。
- **T2（收敛应用）**：同一份 artifact 重复投递（update、restart 重拉）收敛
  到同一状态、无副作用累积；条目内容变化时（同 name 的 skill 指到新的
  store 对象）旧内容被替换而非并存。**✅ 已确认（2026-08-30）**，对应
  `engine-convergence-contract.zh-CN.md` A1/A3。
- **T3（engine config 的创建时序）—— ➖ 移出第一期范围**，问题不会出现：`engine_config` 类别**不落 artifact
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
- **T4（`cli_tools`）—— ✅ 已确认（2026-08-31）**：完整的引擎侧规格见
  `teclaw-cli-contract.zh-CN.md`。要点：artifact 顶层新增 `cli_tools` 数组
  （`{name, store, path, md5, version}`），**一个条目 = 一个命令 = 一个文件**
  ——拉取、`sha256` 强校验、解包、取文件全部在平台侧完成；**`schema_version`
  不升版**，靠「未知字段忽略」兼容。
  引擎侧的五条要求（放置、按 `md5` 判断是否需要重新落地、可执行位、PATH、
  全量覆盖）已作为**要求**写入契约 §3.4，不再作为待确认项——**落点、PATH 的
  具体做法与沙箱策略属 teclaw 自治，平台不过问、也不记录**。
  `md5` 的定位已修订为**变更判据**（同名工具 `md5` 未变即无需重下/替换），
  **不是**落地前的拒绝门。
- **T5（可选优化，不阻塞任何排期）**：目录型资源默认在 compose 时逐文件
  展开为 `ResourceRef`（契约零改动）；若贵侧确认 `ResourceRef.path` 可
  引用**目录子树**（`SkillRef` 已有目录先例），大目录场景 artifact 可以
  更紧凑。不确认则维持逐文件展开。

v2 才可能涉及的唯一契约增量：**条目级应用结果上报**（哪个 skill 装成功了）。
v1 先以 publish-poll 的整体成败 + 平台侧 apply report（fetch/物化环节的
条目级结果）覆盖，不要求 teclaw 提供。

## 4. ARCA 系引擎：2 项确认（A1–A2）

声明式部分零改动：apply 落成的实体与用户今天手工调 TC Open API 的结果无
差别，交付通道现状不变。script 部分即 #935 现状。

- **A1（交付时序）**：新建容器场景下，「manifest 实体的物理交付完成」需要
  先于「script 阶段执行」（design §3.4 的顺序保证——脚本可以假定声明的
  skill / identity 已就位）。identity / skills 的交付若依赖 NAS 持久化，
  重建后天然满足；若存在依赖「对活容器 push」的实体类别，需与 backend 一起
  确认其在启动链中的时点（backend 实现注意项 design §10.1，由 backend
  先行内部确认）。
- **A2（`cli_tools` 的落点与暴露，W9 已改写为下面这套）**：早先此项的问法是
  「平台工具目录在哪个注入点进 agent 进程的 PATH」——即把落点当成平台侧的答案、
  逐引擎去谈。**W9 把它反过来了**：落点是**引擎的**，平台不参与。

  引擎需要提供五个按**命令名**寻址的端点（后端通过各 bot 的 engine adapter
  调用，与 runtime-layout probe、cron relay 走的是同一条通道）。前两个是
  「改一个」，第三个是「整体替换」，后两个是只读的：

  | 端点 | 请求体 | 语义 |
  | --- | --- | --- |
  | `POST /api/cli/install` | `{name, size_bytes, content_b64}` | 「让这个 bot 有 `name` 这个命令」，其余命令不动。**目录、可执行位、以及让 agent 能用到它，全在这一次调用里由引擎完成。**平台不发 `chmod`、不跑 shell |
  | `POST /api/cli/delete` | `{name}` | 移除该命令；本来就没有也算成功 |
  | `POST /api/cli/replace` | `{tools: [{name, size_bytes, content_b64}, …]}` | 「这个 bot 的命令集**就是**这些」。**请求体里没出现的命令要被删掉**——删除是隐含的。空数组是合法且有意义的请求：等于「这个 bot 没有任何命令」 |
  | `GET /api/cli/list` | — | 引擎认为这个 bot 有哪些命令。**仅供漂移观测**，平台的表才是「已安装」的定义。每条返回 `{name, md5, size_bytes}`；**`md5` 必填**——只报 `name` 只能发现「少了 / 多了」，发现不了**同名但二进制被换掉**，而那恰恰是最值得发现的一种。一个命令都没有时返回 `{"tools": []}`，不是 404 |
  | `GET /api/cli/download?name=…` | — | 取回单个命令的字节（base64）。**核对与排查用的旁路，不是交付链路的一环**——平台自己留了一份字节，交付路径上不从容器回读。命令不存在时返回 `200` + `success:false` + `error:"not_found"`，**不用 404**（见下） |

  **`replace` 的响应必须逐命令给结论**，这是它比前三个端点难做的地方，也是
  必须写在契约里而不是留到实现时才发现的一点：

  ```json
  {"success": true,
   "data": {"results": [{"name": "mycli", "success": true},
                        {"name": "othercli", "success": false,
                         "message": "not an executable"}]}}
  ```

  平台的 apply 报告是**按声明条目**给的。如果一次批量调用只回一个总结论，
  四个工具里究竟哪个被引擎拒了就丢失了。因此平台对该响应**严格解析**：请求里
  发过的每个 `name` 都必须在 `results` 里有结论，**少一个就整体当作不可读并报
  错**——把沉默当成功，正是「报告显示装好了、bot 上其实没有」的由来。请求里
  没发过的 `name` 出现在 `results` 里则被忽略（那属于 `list` 该反映的漂移）。

  为什么需要「整体替换」而不是让平台循环调 `install` / `delete`：manifest apply
  的语义就是全量覆盖，而循环会把**中间状态**发到线上——平台先删后装，容器会
  先收到「工具没了」再收到「工具回来了」。一次调用直接给终态，就没有这个窗口。

  约定：非 2xx 抛错、200 但 envelope 里 `success: false` 同样算拒绝——引擎装
  不上的工具，平台**绝不**记成已安装。404 的含义是「这个引擎构建没有 CLI
  端点」，不是「工具不存在」（平台从不按路径问工具）——所以 `download` 遇到
  未知命令时**不能**用 404，否则一个写错的命令名会被读成「整个引擎坏了」。
  字节以 base64 走 JSON body：`DeviceAdapterTransport` 是 core 能绑定也能测试
  的唯一引擎通道，逐文件写用的 multipart 通道其 ARCA 分支是 corp-only。

  **本仓库的实现按能力位拒绝时返回 `501` 而不是 `404`**（`api/caps.py` 的
  `check_capability`，与其余所有 router 一致）。两者在平台侧都落成
  `CliToolPlacementError`，都不可能被误读成成功；沿用 `501` 是为了让引擎内部
  一致，而 `501`（未实现）本身也比 `404`（找不到）更贴近「这个构建不做
  CLI」。契约在此以实现为准。

  **v1 已知代价：`replace` 会带上集合里每一个工具的字节，哪怕这次只改了一个。**
  四个工具改一个 = 四份二进制都重传。之所以可接受：**完全没变化的 apply 根本
  不会调到这个端点**（平台先按 `(digest, subpath)` 收敛，全都没变就一次都不调），
  所以代价只落在「确实改了东西」的那次 apply 上。后续优化要动契约——集合按
  `(name, digest)` 传、只对引擎说缺的那些补字节——因此留到 v2 一起谈。

  **目录常量归引擎**，平台代码里没有它的副本。openclaw 侧的建议值是
  `/home/admin/.openclaw/cli`；每个 ARCA 引擎有自己的一份，默认技能集本来就是
  按引擎分的，所以「告诉 agent 去哪找」这件事天然也按引擎走。

  **v1 不做 PATH 注入**，这是一项明确的取舍：agent 由默认技能集里的一个 skill
  被告知落点，并以**绝对路径**调用。代价是 `mycli --help` 不工作、每次调用都
  依赖那个 skill 被读到、脚本内 shell 调同目录的另一个工具也找不到。
  之所以敢推后：把目录加进 PATH 是**引擎侧**改动，不牵动 schema、管理 API、
  `ac_bot_cli_tool` 表或 artifact 契约中的任何字段。已知先例仍然有效——
  singlebox 的 `bots_dynamic_start_openclaw` 把 `bcs-cli` 所在目录加进
  openclaw gateway 的 PATH（`scripts/modules/bots.sh`）。

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
| O5 | strict 就绪门控（配置应用失败 = 不就绪）是否 v2 做 | v2 再议，两家族必须同步 | 平台 + 两引擎家族 |
| O6 | 模板级 manifest（一份声明 → 多个 bot） | v2；v1 仅 bot 级 | 业务 |
| O7 | `center://` skill-center 引用源 | v2 | backend |
| O8 | 凭证注入方式是否需要请求头之外的形态（query 参数 token / mTLS） | v1 仅请求头；有真实业务源依赖再评估 | backend + 业务 |
| O9 | `cli_tools` 的目标架构：容器架构是否统一（x86_64），是否需要多架构源（per-arch URL / `${BOT_ARCH}` 变量） | 先确认容器架构现状；v1 单 URL | backend + 业务 |
| O10 | engine plugin 类目（openclaw extensions / claude_code plugins）如何声明化 | v2；方向定为**注册表引用**（照 MCP 模子，不走任意 URL——插件在引擎进程内自动执行，供应链敏感度最高）。两项引擎侧前置确认：openclaw extensions 目录作为落点是否成立；claude_code 的同步规则**刻意排除 `plugins/`** 的原因 | backend + 两引擎 + 业务 |
| O11 | 公司 git 托管服务（业务内容源）的能力确认：① 有无仓库级只读 token（Project/Deploy Token 类；无则机器人账号方案是否合规）；② 归档 API 能否按 ref + 子目录取 tar/zip，配额如何；③ refs 解析 API 形态、tag 有无不可变保护；④ 鉴权头形态（`PRIVATE-TOKEN` / `Authorization`）；⑤ 平台侧网络可达性 | 走 API 编译为归档拉取（design §10.5）；①②不成立再评估 clone / 退回 zip | backend + git 托管方 + 业务 |

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
7. **目录资源的传输形态**：内容托管在公司 git 服务上时优先 **git 源**
   （schema §2.2——免打包、tag 即版本）：请确认仓库归属与 tag 发布流程，
   并配合 O11 的托管服务能力确认；凭证按统一形状注册（`allowed_prefixes`
   必填，收到仓库粒度；token 不用个人 PAT；secret 加密落库、主密钥存密钥库
   ——schema §2.1）。非 git 源仍可用归档
   （zip/tar.gz，schema §3.2；打包习惯决定 `strip_components` 用不用）。
8. **CLI 工具的形态**：想装的工具具体是什么——**自包含的静态二进制**
   （v1 范围内；压缩包只是传输形态，用 `subpath` 取出其中**一个**文件，
   包内其余文件不下发），还是需要同包辅助程序/`lib/` 的多文件工具（**v1
   不支持**，请打成自包含二进制），还是 npm/pip 包（属命令式，走 script，
   且 teclaw 不可用）？目标容器架构是否单一（对应 O9）？注意 `cli_tools`
   是唯一**不建议进 git** 的类目（二进制制品走制品库 + digest，
   schema §1.1）——工具的分发地址请单独提供。
9. **仓库拓扑与发版流程**：identity / skills / resources 是同一个仓库
   （命名源一处声明即可，一个 tag 原子升级整套——schema §2.3），还是
   分散在多个仓库（则需多个命名源，凭证的 `allowed_prefixes` 相应列出
   多个前缀）？tag 命名与发布节奏是什么（是否会重打同名 tag——
   重打即声明含义变化，下次 apply 收敛到新内容）？
10. **配置本身是否也想进 git**：v2 方向是配置清单文档自身托管于仓库、平台只
   存指针（design §9.1，可顺带解掉模板级 manifest）。若这是你们期望的
   终态，请提出——它会影响 v1 的 API 设计取舍。
