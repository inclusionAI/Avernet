# 跨引擎收敛语义契约：应用一份 manifest 会对已经存在的东西做什么

> **给 teclaw owner 与 ARCA 系引擎的双边契约。**
>
> 一句话：**你们收到的 artifact 是完整期望状态，整包替换即可；唯一不能碰的是
> `MEMORY.md` 与 `IDENTITY.md`。**
>
> 本文档把规则写成对 **applier** 的**要求**，而不是对平台实现的描述——因为
> 在 teclaw 上，applier 不是我们。**§3 先划清一条界：哪些规则到得了你们手上，
> 哪些根本到不了。**

---

## 1. 为什么需要这份契约

平台正在做 **Bot Config Manifest**：用户提交一份声明式配置清单，平台在生命周期
边界上把它落成真实实体（skills / identity / resources / mcp …）。

问题在于「落成」这个动作的最后一步不在同一个地方：

- **BaaS / ARCA 系**——平台自己写文件、自己对齐 skill set，收敛语义归我们；
- **teclaw**——平台把整包 `BotConfigArtifact` 递交过去，容器内怎么应用归你们。

今天没有任何东西让这两侧一致。**而这种不对称在咬到用户之前对用户不可见**：同一份
manifest、同一次修改，在两种 bot 上得到不同结果，用户既没有办法预期，也没有办法
排查。这份契约就是把它变成一件被写下来、被双方同意的事。

**这不是一场谈判。**核心问题已经有答案：**teclaw 的重新下发是全量覆盖**（与
teclaw owner 确认，2026-08-30）。平台侧的收敛策略**正是因此**采纳了同一套语义，
而不是发明第二套再去调和。所以下面每一条，对 teclaw 而言基本都是**对现有行为的
陈述**。

> **状态（2026-08-31）：§4 的 A1–A5 已全部确认，本文档没有开放问题。**逐条结论
> 见 §7。

---

## 2. 谁是 applier

| 引擎系 | applier | 它收到的输入 | 谁实现本契约 |
| --- | --- | --- | --- |
| **BaaS / ARCA 系**（openclaw / claude_code / aicoding / hermes / moltis） | 平台（backend） | manifest 文档本身 | 我们 |
| **teclaw** | teclaw 引擎 | 整包 `BotConfigArtifact` | 你们 |

**「applier」= 把一份声明变成容器内实际状态的那一方。**§4 的 **A 条**是对它的要求；
§6 的 **P 条**是平台侧的，不需要你们实现。

> **下发契约不变。**manifest 特性对 artifact 如何递交、`{store, path}` 的形态、
> `engine_ext` 的不透明约定**不做任何改动**。teclaw 侧唯一的新增是 `cli_tools`
> 段，单独规格见 `teclaw-cli-contract.zh-CN.md`。

---

## 3. 先讲清楚层次：哪些是对你们的要求，哪些不是

本文档早先的版本把两层规则混在一起写成「对 applier 的要求」，那是错的，先更正
再往下讲——因为搞错这一层，会让你们去实现一些根本到不了你们手上的语义。

```text
   ① manifest → 平台实体（apply）        ← 平台侧，applier 是我们
      「被声明的类目覆盖，未声明的不碰」都发生在这一层
                    │
                    │  compose：从平台实体读出**当前全量状态**
                    ▼
   ② BotConfigArtifact（整包快照）→ 引擎  ← 你们从这里接手
      每个类目**永远完整出现**；不存在「未声明」这回事
```

**关键事实：artifact 是全量快照，不是 manifest 的差分。**`ConfigComposer` 是从
**平台实体**（DB 状态）读出来组装 artifact 的，不是从 manifest 读。所以 artifact
里的 `skills` 永远是这个 bot 的**完整** skill 集合，与「这次 manifest 声明了什么」
无关；`resources`、`identity_files`、`mcp` 同理。

**因此：「未声明的类目不碰」不是你们要实现的规则**，你们也无从实现——你们收到的
每一份 artifact 都是完整的期望状态。**整包替换就是正确行为。**

---

## 4. 对 applier 的要求

以下才是需要你们实现（或确认已经如此）的。

### A1 — 整包替换：artifact 就是完整期望状态

收到一份 artifact，容器内的状态必须变成**恰好**是它描述的样子。每个类目的区域
（§5）内，artifact 没有列出的东西被移除。

不做合并、不做增量：artifact 里 `skills` 是 `{A, B}` 而容器里原本是 `{B, C}`，
结果必须是 `{A, B}`，`C` 消失。

> ✅ **已确认**（teclaw owner，2026-08-30）：teclaw 的重新下发本来就是全量覆盖。
> 这一条是把既有行为写成契约，不是新要求。

### A2 — 保留名：`MEMORY.md` 与 `IDENTITY.md` 永不写、永不删

**这是 A1 唯一的例外，也是 applier 绝不能碰的唯一一件事。**它们是引擎生成的运行期
状态，不是配置：

- 不得因为任何条目而**写入**这两个文件——平台在 `PUT` 时就拒绝声明它们，所以合法
  的 artifact 里不会出现；
- 不得因为 A1 的替换而**删除**它们——即使 `identity_files` 里没有它们。它们在
  替换之外。

> **这一条取代了一份被撤销的要求。**早先的版本要求 applier「保留新旧两个版本都没
> 声明的所有文件」——那是一个**无界集合**，我们既无法替你们计算，也无法验证。两个
> 具名文件是一份能被同意、也能被检查的契约。
>
> ✅ **已确认。**

### A3 — 收敛：应用 N 次 = 应用 1 次

同一份 artifact 重复投递，结果必须相同，且不得有副作用堆积（残留文件、重复条目、
版本堆叠）。重复投递是**正常情况**，不是错误。

> ✅ **已确认**（同 A1）。

### A4 — 配置先于就绪

必须在报告 bot **ready 之前**完成整份 artifact 的应用。不允许出现「已经 ready 但
配置只应用了一半」的窗口。

> ✅ **已确认**（T1）。

### A5 — 未知字段忽略，不报错

契约会继续演进。遇到不认识的字段应当忽略，而不是拒绝整份 artifact。

> ✅ **已确认。**这一条现在承担了实际作用：见 §7 —— **`schema_version` 维持 `4`
> 不升版**，`cli_tools` 将作为一个新增字段直接出现，由本条保证旧引擎不会因此报错。

---

## 5. 「区域」是逐类目定义的，不是全局的

A1 的替换需要一个作用域，而**这个作用域对每个类目并不是同一个形状**。搞错这一点，
一条本意是收敛 skill 列表的规则就会删掉 bot 的工作目录。

| 类目 | 被替换的区域 | 备注 |
| --- | --- | --- |
| `skills` | **active skill set** | 等于 artifact 列表；未列出的被移除 |
| `identity_files` | **identity 文件集合，减去 A2 的保留名** | claude_code 的合法集仅 `CLAUDE.md` |
| `resources` | **你们此前投递过的 resource 路径 ∪ 本次 artifact 列出的路径** | 见下方警告 |
| `mcp` | **已启用的 server 集合** | |
| `cli_tools` | **由平台下发的命令集合** | 不影响镜像自带命令。详见 `teclaw-cli-contract.zh-CN.md` |
| `script` | —— | **teclaw 不支持**，平台在写入时即拒绝；ARCA 系走 #935 既有启动链 |

> ### ⚠️ `resources` 的区域有两条边，缺哪条都是 bug
>
> 它是唯一一个不能用「本次 artifact 列了什么」单独定义区域的类目，**两个方向都要
> 守住**：
>
> **① 不能读窄。**区域必须**包含你们此前投递过的 resource 路径**，哪怕它们不在
> 本次 artifact 里。否则 A1 的「完整期望状态」在这个类目上落不了地：
>
> - `resources` 从 `[data/a.csv]` 变成 `[]` —— 若区域只由本次 artifact 决定，区域
>   就是空的，`data/a.csv` 永远删不掉，而声明说它不该存在了；
> - 所有资源整体搬到另一棵子树 —— 旧子树不在本次区域内，旧文件留在原地，于是同一
>   个文件同时存在于新旧两处。
>
> 这与 `cli_tools` 的规则（§3.4 第 5 条：上一版投递过、这一版不在数组里的，移除）
> 是同一条，只是那边的单位是命令名，这边是路径。**判据是「这个文件是不是我作为
> resource 投递的」，不是「它在不在本次列表里」。**
>
> **② 不能读宽。**除此之外一律不碰——**workspace 是 bot 的工作区，不是平台的**。
> 用户自己或模型在容器里创建的文件、工作产物、临时目录，都不在区域内，即使它们
> 与某个 resource 路径相邻甚至同名前缀。把这一边读宽一格，就是删掉用户的工作成果。
>
> 这个集合是**有界且可核对的**——它就是你们自己投递过的东西，与被撤销的那条
> 「保留两个版本都没声明的所有文件」不同，那才是无界的。

`engine_config` **不在第一期范围内**（T3），且它不走 artifact 字段——经既有的
逐文件通道写 `config/teclaw.json`。

---

## 6. 平台侧的规则（不需要你们实现，写在这里是为了让你们理解 artifact 怎么来的）

这些发生在 §3 的第 ① 层，**在 artifact 被组装之前**。列出来只是为了让「为什么这次
artifact 长这样」可解释，你们不需要为它们做任何事。

| | 规则 |
| --- | --- |
| **P1** | manifest 里被**声明**的类目，其平台实体被收敛到等于声明 |
| **P2** | manifest **没声明**的类目，平台完全不碰——它的实体保持原样，因而在下一份 artifact 里照原样出现 |
| **P3** | `skills: []` 这种**显式空集合本身是一种声明**，意思是「全部移除」；与「没声明」不同 |
| **P4** | **类目是 all-or-nothing 的**：声明类目里只要有一条无法物化（拉取失败等），该类目整个不动。覆盖语义下不完整的集合是破坏性的——声明 `{A, B}` 却只写入 `{A}` 就等于删了 `B`，所以只从完整目标状态写入 |

**P2 与 P4 对你们的可见影响只有一个：**某次 manifest 变更之后，artifact 里某个类目
可能**与上次完全相同**。那不是「没发下来」，而是平台侧本来就没动它。按 A1/A3 处理
即可，不需要特殊分支。

---

## 7. 状态：逐条结论

**2026-08-31 更新：本文档的开放问题已全部关闭。2026-09-02：新增 §9 `ownership`，待确认。**

| | 结论 |
| --- | --- |
| **A1** 整包替换 | ✅ 已确认（teclaw owner，2026-08-30）。X2/T2 关闭 |
| **A2** 保留名 `MEMORY.md` / `IDENTITY.md` | ✅ 已同意 |
| **A3** 收敛 | ✅ 已确认 |
| **A4** 配置先于就绪 | ✅ 已确认（T1） |
| **A5** 忽略未知字段 | ✅ 已同意 |
| **`schema_version`** | ➖ **维持 `4`，本期不升版。**`cli_tools` 直接作为新增字段出现，靠 A5 保证兼容——这正是不升版的前提 |
| **`cli_tools` 的引擎侧行为** | 见 `teclaw-cli-contract.zh-CN.md` §3.4 —— 那是**要求**，不是问题 |
| `engine_config` 首启行为（T3） | ➖ 移出第一期范围，问题不会出现 |
| 目录型 `ResourceRef`（T5） | ➖ 可选优化，不排期 |
| **`ownership`（§9，W8）** | ⏳ **待 teclaw 确认** R-O1 / R-O2 / R-O3。确认前平台开关关闭，artifact 行为不变 |

**这份契约的分量，写明一次：**平台侧之所以采纳整包替换语义，**正是因为** teclaw 已经
这么做。如果这一点是错的，平台侧整个收敛策略都得推翻。所以 A1 不是一条我们希望你们
配合的规则，而是一个**对我们的设计起承重作用的假设**——它必须被确认，不能被继承。
现在它被确认了。

**如果后续出现你们不打算实现的部分**，写进**能力矩阵**（`engine-requirements.zh-CN.md`
§2），让差异是被记录的，而不是被用户撞见的。

---

## 8. 自查清单

供实现方自查，也是平台侧联调时会验证的点：

- [ ] 应用一份 artifact 后，各类目区域内**未被列出**的旧内容确实消失了（A1）。
- [ ] `MEMORY.md` 与 `IDENTITY.md` 在**任何**一次应用之后都还在，内容未被改写（A2）。
- [ ] 特别地：`identity_files` 不含这两个文件时，它们**仍然存在**（A2）。
- [ ] `resources` 从 `[data/a.csv]` 变成 `[]` 后，`data/a.csv` **确实被删除**（§5 ①）。
- [ ] `resources` 整体搬到另一棵子树后，旧路径下**没有残留**，新旧不并存（§5 ①）。
- [ ] 与此同时，非平台投递的工作区文件（用户/模型产生的）**未被触碰**（§5 ②）。
- [ ] 同一份 artifact 连续应用两次，第二次没有产生任何变化，也没有残留（A3）。
- [ ] bot 报告 ready 时，整份 artifact 已经应用完毕（A4）。
- [ ] 含未知字段的 artifact 被接受，未知字段被忽略（A5）。
- [ ] 某个类目与上一份 artifact 完全相同时，行为与其他情况一致，无特殊分支（§6）。

---

## 9. 平台管理的类目：`ownership`（W8 新增，待 teclaw 确认）

> **2026-09-02 新增。**本节是 W8（#1476）带来的**唯一**一处 artifact 契约变化，
> 与 `cli_tools` 一样直接进入 `schema_version` 4，靠 A5 兼容。它把 §6 的 P1/P2
> ——「声明的类目收敛到等于声明；没声明的完全不碰」——**写到线上**。

### 9.1 为什么需要它

今天的 artifact 对 `identity_files` / `resources` 只能表达「一个列表」，表达不了
「这个列表是平台的完整期望状态」还是「平台对这个类目没有意见」。于是引擎只能猜：
草稿 artifact 一直带着空的 `identity_files` / `resources` 重投给运行中的 bot，而
bot 的文件没有丢——说明引擎把空列表当成「别动」。这与 A1 的字面（空 = 全删）并不
一致，也让平台**无法**在 artifact 里表达「manifest 声明 `identity: []`」。

W8 之后平台是 manifest 所应用内容的**真相源**（两个引擎系一致）：manifest 声明的
文件类目由平台物化进 OSS（`bot-data` store）并在 artifact 里以 `{store, path}`
引用下发——**首份 artifact 就带着**，之后每次 manifest 变更都整包重投。要让引擎
分得清「平台在断言」与「平台没意见」，需要一个显式标记。

### 9.2 字段

顶层新增可选对象 `ownership`，键是类目字段名，值二选一：

```json
{
  "schema_version": 4,
  "ownership": {
    "mcp": "platform",
    "skills": "platform",
    "resources": "platform",
    "identity_files": "engine"
  },
  "identity_files": [],
  "resources": [{ "name": "kb/faq.md", "store": "bot-data",
                  "path": "staff_u1/bot7_manifest/teclaw/workspace/kb/faq.md" }],
  "skills": [{ "name": "order-lookup", "scope": "user", "store": "bot-data",
               "path": "staff_u1/bot7_manifest/teclaw/workspace/skills-local/order-lookup" }],
  "...": "..."
}
```

| 值 | 含义 | 对应 §6 |
| --- | --- | --- |
| `platform` | **本次 artifact 里该类目的列表就是完整期望状态。**按 §5 的区域做 A1 替换；空列表 = 区域内全部移除 | P1 / P3 |
| `engine` | **该类目由引擎管理。**忽略 artifact 里的列表，保持引擎自己的状态 | P2 |
| 缺席（整个对象缺席，或某个类目缺席） | **W8 之前的行为不变。**这是 A5 的用武之地：没实现本节的引擎照旧运行 | —— |

平台侧的规则：**`ownership` 跟着操作走，不跟着 manifest 的声明走。**manifest
apply 结束时的整包重投、以及带 manifest 的 bot 的第一份 artifact（创建 job 先 apply
再开容器），**所有类目**都是 `platform`——artifact 里的列表就是完整期望状态；
其他任何操作触发的组装（上传 skill、上传资源、改 MCP、改渠道、发布构建）**所有
类目**都是 `engine`——引擎自己的状态是真相。`mcp` 在任何操作下都是 `platform`
（artifact 自 W12 起每次都带完整 MCP 集合，引擎没有自己的 MCP 状态可保留；这只是
把今天的语义写明，不是变化）。一个没有 manifest 的 bot 拿到的 artifact 与今天逐字节
相同，只多这一个对象且除 `mcp` 外全为 `engine`。

### 9.3 对引擎的三条要求

- [ ] **R-O1 `ownership` 语义。**按 9.2 的表执行；未知类目键忽略（A5）。
- [ ] **R-O2 运行中容器的整包重投要落文件。**今天只有新容器在启动时按引用从 store
  拉文件；W8 之后 `PUT manifest` 会向**运行中的** bot 重投带 `identity_files` /
  `resources` / `skills` 引用的 artifact，引擎必须按 §5 的区域把它们落地并收敛
  （多的删、少的拉），且仍满足 A3（重复投递无副作用）。
- [ ] **R-O3 store 后端的本地 skill。**一个 manifest 安装的本地 skill 在 `skills` 里
  是一条 `SkillRef{scope: "user", store: "bot-data", path: <包目录前缀>}`（`scope`
  沿用 artifact 既有的 `shared | user` 词汇；新的是它有了 store 地址）。引擎按
  `path` 前缀从 store 拉整个包目录；`skills` 区域（active skill set）的 A1 替换同样
  适用于它。**仅当 `resources` 也是 `platform` 时**，包内文件还会同时以 `resources`
  引用出现在 `workspace/skills-local/<name>/…` 下（发布 gather 的形状）；`resources`
  为 `engine` 时列表里不会有它们——一个引擎会忽略的列表不带任何平台断言的文件。

### 9.4 平台侧的开关

引擎支持以上三条之前，平台的 `teclaw_platform_managed` 开关默认**关闭**：关闭时
teclaw 的 manifest 走 W8 之前的逐文件通道，artifact 与今天逐字节相同（只多一个
全为 `engine` 的 `ownership`，A5 保证无害）。开关打开的条件就是本节 9.3 三条被
确认。

### 9.5 自查（追加到 §8）

- [ ] `ownership.identity_files = "platform"` 且列表为空时，identity 区域内除
  `MEMORY.md` / `IDENTITY.md` 外的文件**确实被移除**（A2 仍然成立）。
- [ ] `ownership.resources = "engine"` 时，artifact 里的 `resources` 列表**不产生任何
  效果**，容器里的工作区文件一个不少。
- [ ] 缺少 `ownership` 的 artifact 行为与 W8 之前**完全一致**。
- [ ] 向运行中的容器重投带文件引用的 artifact，文件按区域落地并收敛（R-O2）。
- [ ] `skills` 里 `scope: "user"` 且带 `store` 地址的引用被拉取为完整包目录并激活（R-O3）。

---

## 附：相关文档

| 文档 | 内容 |
| --- | --- |
| `teclaw-cli-contract.zh-CN.md` | **给 teclaw 的 `cli_tools` 规格**——本文档之外唯一需要你们新增实现的东西 |
| `engine-requirements.zh-CN.md` §2 | 能力矩阵：分歧记录在这里 |
| `design.zh-CN.md` | Bot Config Manifest 总体设计 |
| `manifest-schema.zh-CN.md` | 用户侧 manifest 的 schema |
| `work-items.zh-CN.md` §3.2 / W12 | 本契约的平台侧论证与实现工作项（§3.2 = 本文 §6 的 P 条） |
| `kernel/bot_config/artifact.py` | `BotConfigArtifact` 的代码定义 |
| `specs/2026-09-02-manifest-lifecycle-apply-points/` | W8 的 spec / plan：§9 的平台侧论证（D-3、D-4、D-5） |
