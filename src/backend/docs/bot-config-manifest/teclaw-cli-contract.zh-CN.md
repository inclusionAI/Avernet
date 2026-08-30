# teclaw 引擎契约增补：`cli_tools`

> **给 teclaw owner 的实现说明。**
>
> 一句话：**下发契约不变，只新增一个 `cli_tools` 段。**其余部分（artifact
> 的组装、投递方式、`{store, path}` 引用形态、`engine_ext` 的不透明约定）
> 全部保持现状，本文档不改动其中任何一条。
>
> 需要 teclaw 侧实现的，只有「把 `cli_tools` 里声明的可执行物放进容器，并
> 保证它在 agent 进程的 PATH 上」。

---

## 1. 为什么需要这个

平台正在做 **Bot Config Manifest**：用户提交一份声明式配置清单，平台在生命
周期边界上把它落成真实实体（skills / identity / resources / mcp …）。其中一
类是**命令行工具**——用户希望模型能直接调用自己提供的 CLI。

这个能力在仓库里已经有手工版本：singlebox 的编排脚本把 `bcs-cli` 二进制挂到
PATH 上，再配一个 SKILL.md 教模型怎么用。`cli_tools` 是把这套「二进制 +
用法说明」的双件套产品化、声明化。

**ARCA 系**由平台注入 PATH（我们自己实现）。**teclaw** 的容器由你们管理，
平台不进容器，所以这一段需要你们实现——这是本文档存在的唯一原因。

---

## 2. 不变的部分（明确声明，避免误读）

以下全部**维持现状，本次不动**：

| | 现状 | 本次是否改动 |
| --- | --- | --- |
| artifact 的投递方式 | `deploy_config.teclaw_bot_config` 承载完整 artifact | **不变** |
| 重新下发的语义 | **全量覆盖**（已与 teclaw owner 确认，2026-08-30） | **不变** |
| 文件引用形态 | `{store, path}`，`store` 索引到 `stores`，物理落点由引擎决定 | **不变**，`cli_tools` 沿用同一形态 |
| `stores` | 只放位置坐标，**永不放凭证** | **不变** |
| `engine_ext` | 引擎自有的不透明字段，平台原样存取、绝不解释 | **不变** |
| `identity_files` / `resources` / `skills` / `mcp` | 各自现有语义 | **不变** |

**唯一的改动是 artifact 顶层新增一个 `cli_tools` 数组**，以及随之而来的
`schema_version` 从 `4` 升到 `5`。旧 artifact（无 `cli_tools`）在新引擎上必须
继续可用——见 §6 兼容性。

---

## 3. 契约增补

### 3.1 artifact 顶层新增字段

```jsonc
{
  "schema_version": 5,          // 4 → 5
  "engine_type": "teclaw",
  "skills": [ … ],              // 不变
  "resources": [ … ],           // 不变
  "identity_files": [ … ],      // 不变
  "mcp": { … },                 // 不变
  "stores": { … },              // 不变
  "engine_ext": { … },          // 不变

  "cli_tools": [                // ★ 新增
    {
      "name": "mycli",
      "store": "bot-data",
      "path": "staff_u1/bot7_42_publish/teclaw/cli/mycli",
      "version": "1.4.2",
      "entrypoints": ["mycli"]
    }
  ]
}
```

### 3.2 `cli_tools` 条目字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | ✅ | 工具标识符。**不含路径信息**，仅用于标识与日志 |
| `store` | string | ✅ | 索引到 `stores` 的 key，与 skills / resources 同一机制 |
| `path` | string | ✅ | 相对 `store` 的路径。**指向一个已解包的目录**（见 §3.3） |
| `entrypoints` | string[] | ✅ | `path` 目录内**哪些相对路径应当作为命令暴露**。至少一项。**保证被限制在 `path` 之内**——见 §3.3.1 |
| `version` | string | ❌ | 元数据，仅用于审计与展示。引擎不需要理解 |

### 3.3 平台保证：下发的一定是「已解包的目录」

这一条是为了让你们那边尽量简单——**解包、校验、格式判断全部由平台完成**：

- 用户在 manifest 里可以写单个二进制，也可以写 `.tar.gz` / `.zip` 压缩包。
- 平台负责拉取、**强制校验 `sha256` digest**、解包、并把结果作为一个**目录**
  写入 store。
- 所以 artifact 里的 `path` **永远指向目录**，即使用户只声明了一个裸二进制
  （此时目录里就一个文件，`entrypoints` 就是 `["mycli"]`）。

**引擎侧不需要处理压缩包、不需要校验 digest、不需要判断文件格式。**

### 3.3.1 平台保证：`entrypoints` 一定落在 `path` 之内

`entrypoints` 决定了哪些文件会被**置为可执行并暴露到 PATH 上**，所以它的取值范围
必须是被约束的，否则一个形如 `../../some/file` 的条目就会让引擎去 chmod 一个工具
目录之外的文件。

**平台在构造 artifact 之前完成校验**，`PUT` 时即拒绝：

- 绝对路径；
- 含 `..` 的路径穿越；
- 规范化（解析符号链接）之后**逃出 `path` 子树**的条目；
- 在解包结果中**不存在**的路径；
- 不是**普通文件**的路径（目录、符号链接、设备文件等）。

所以到达引擎的每一个 `entrypoints` 值，都是 `path` 目录内一个已存在的普通文件的
相对路径。

> **建议你们也校验一次。**这是纵深防御，不是不信任：如果某个 `entrypoints` 在你们
> 那边规范化后落到工具目录之外，**拒绝它并报错**，不要 chmod 或建链。平台侧的校验
> 是契约保证，引擎侧的校验是它的兜底。

### 3.3.2 平台保证：暴露出来的命令名全局唯一

一个 entrypoint 暴露出来的命令名，就是它的 **basename**：`bin/tk` → `tk`。

如果两个 entrypoint 的 basename 相同——无论是同一个包内的 `bin/tool` 与
`helpers/tool`，还是两个不同工具各自的 `tool`——它们**不可能都**按名字直接调用：
必然有一个目录或软链遮住另一个，谁赢取决于安装顺序。

**平台在 `PUT` 时就拒绝这种声明**，检查范围是该 bot 的**所有** `cli_tools` 条目合
起来。所以到达引擎的每一份 artifact，其全部 entrypoint 的 basename 两两不同。

> v1 不提供命令别名（`expose_as` 之类）。需要两个同名工具时，用户改其中一个的文件名
> 或换个包 —— 这比引入一个别名字段简单，而且把「叫什么名字」留在用户手里。

### 3.4 需要 teclaw 实现的行为

1. **放置**：把 `path` 指向的目录内容取到容器内的某个位置。**具体落在哪里由
   你们决定**——和 skills / resources 一样，平台不规定物理路径。

2. **可执行位**：`entrypoints` 里列出的每个相对路径，在容器内必须是**可执行**
   的。这一点平台无法代劳：对象存储不保留 POSIX 权限位，所以执行位必须由取
   下来的一方设置。**只对 `entrypoints` 列出的路径设置执行位**——包内其余文件
   按原样落地即可，不应被批量置为可执行。

3. **PATH**：`entrypoints` 里列出的命令，必须能被 **agent 进程**直接按名字调用
   （即 `mycli` 而不是 `/some/abs/path/mycli`）。做法由你们决定——放进一个已
   在 PATH 上的目录、建软链、或把工具目录追加进 PATH 都可以。

   **命令名 = entrypoint 的 basename**，而平台保证这些 basename 在一个 bot 的所有
   `cli_tools` 条目之间**全局唯一**（见 §3.3.2）。所以你们不需要处理重名冲突，也
   不需要定义「谁覆盖谁」的规则——那种情况在到达你们之前就被 `PUT` 拒绝了。

4. **覆盖语义**：`cli_tools` 与其他类目一样遵循**全量覆盖**——artifact 里这个
   数组就是**完整期望状态**。上一版投递过、这一版不在数组里的工具，应当**移除**
   （命令不再可调用）。数组为空 `[]` 表示「不应有任何 manifest 下发的工具」。

   > 这条与你们现有的重新下发语义一致，不是新规则，只是明确写下来。

### 3.5 一个完整的 artifact 片段

```jsonc
{
  "schema_version": 5,
  "engine_type": "teclaw",
  "version": 7,

  "stores": {
    "bot-data": {
      "type": "oss",
      "bucket": "avernet-prod",
      "base": "teclaw/prod/bolt_data"
    }
  },

  "cli_tools": [
    {
      "name": "mycli",
      "store": "bot-data",
      "path": "staff_u1/bot7_42_publish/teclaw/cli/mycli",
      "version": "1.4.2",
      "entrypoints": ["mycli"]
    },
    {
      "name": "toolkit",
      "store": "bot-data",
      "path": "staff_u1/bot7_42_publish/teclaw/cli/toolkit",
      "version": "0.9.0",
      "entrypoints": ["bin/tk", "bin/tk-helper"]
    }
  ],

  "identity_files": [
    {
      "name": "TOOLS.md",
      "store": "bot-data",
      "path": "staff_u1/bot7_42_publish/teclaw/identity/TOOLS.md"
    }
  ]
}
```

解读：

- `mycli` 是单二进制形态。目录 `…/cli/mycli/` 内只有一个文件 `mycli`，
  `entrypoints: ["mycli"]`。落地后 agent 进程执行 `mycli --version` 应当可用。
- `toolkit` 是压缩包形态，平台已解包。目录内有 `bin/tk`、`bin/tk-helper` 以及
  其他辅助文件（例如 `lib/`）。**只有 `entrypoints` 列出的两个需要可执行 +
  上 PATH**，其余文件只需按原样落地（`bin/tk` 可能会在运行时引用 `lib/`，所以
  **目录结构必须保留**，不能只挑出 entrypoints 两个文件）。
- `TOOLS.md` 是普通 identity 文件，走现有通道。它是用户用来告诉模型「有哪些
  命令、怎么用」的——**用法认知不属于 `cli_tools` 的职责**，`cli_tools` 只保证
  命令在 PATH 上。

---

## 4. 用例

### 用例 1：单个静态二进制

用户 manifest：

```yaml
manifest:
  cli_tools:
    - name: mycli
      source: https://my-svc.example.com/tools/mycli-linux-amd64
      digest: "sha256:3f7a…"
      version: "1.4.2"
```

平台做的事：拉取 → 校验 digest → 作为单文件目录写入 store。

artifact 里出现：`{name: "mycli", store: "bot-data", path: "…/cli/mycli",
entrypoints: ["mycli"], version: "1.4.2"}`。

**期望结果**：容器内 agent 进程执行 `mycli` 可用。

---

### 用例 2：压缩包，多个入口

```yaml
manifest:
  cli_tools:
    - name: toolkit
      source: https://my-svc.example.com/tools/toolkit-0.9.0.tar.gz
      unpack: tar.gz
      strip_components: 1
      digest: "sha256:9b21…"
      entrypoints: [bin/tk, bin/tk-helper]
```

平台做的事：拉取 → 校验 digest → 解包（并处理 `strip_components`）→ 整棵目录
写入 store。

**期望结果**：`tk` 和 `tk-helper` 两个命令可用；包内其余文件（如 `lib/`）在
容器内保持相对目录结构不变。

---

### 用例 3：升级（同名工具换版本）

第一次投递 `mycli` v1.4.2；用户改 manifest 到 v1.5.0，平台重新投递。

artifact 里 `mycli` 的 `path` 变了（新的 stage-scoped 路径），`version` 变成
`"1.5.0"`。

**期望结果**：容器内 `mycli` 是新版本。旧版本不应残留、不应出现两个版本共存。

---

### 用例 4：移除

用户从 manifest 里删掉 `toolkit`，只留 `mycli`。新 artifact 的 `cli_tools`
只有一项。

**期望结果**：`tk` / `tk-helper` **不再可调用**，其文件被清理。`mycli` 不受
影响。

> 这就是 §3.4 第 4 条的全量覆盖：数组是完整期望状态。

---

### 用例 5：清空

用户写 `cli_tools: []`（或删掉整个类目后平台判定该类目应为空）。

**期望结果**：所有由 manifest 下发的 CLI 工具都不再可调用。

> 注意：**只清理 manifest 下发的工具**。容器镜像里自带的命令、你们自己装的
> 工具，都不在这个范围内——平台下发的工具应当落在一个可辨识的位置，正是为了
> 让这次清理有明确边界。

---

### 用例 6：重复投递同一个 artifact（幂等）

同一份 artifact 再投递一次，内容完全没变。

**期望结果**：收敛到同一状态，不产生副作用、不重复堆积。这与你们现有的
convergent re-delivery 语义一致，此处只是确认它对 `cli_tools` 同样成立。

---

## 5. 不在本次范围内

明确列出，避免过度设计：

| | 说明 |
| --- | --- |
| **包管理器安装**（npm / pip / apt） | 属命令式领域，不进 manifest。ARCA 侧走 startup script；teclaw 不支持 script，因此这类需求在 teclaw 上暂不支持 |
| **沙箱 / 权限策略** | 用户提供的二进制在容器内的权限边界由 teclaw 决定。平台不做规定，但**如果你们有策略上的顾虑，这是需要提出来的地方**——它的能力面与 script 相邻 |
| **架构适配** | v1 只承诺 `linux/amd64`（ARCA 机群已确认）。多架构分发是后续话题 |
| **用法认知** | 模型怎么知道有这些命令、怎么用——走用户自己声明的 identity（如 `TOOLS.md`）或配套 skill，不是 `cli_tools` 的职责 |

---

## 6. 兼容性

- **`schema_version` 4 → 5。**变更内容仅为新增可选的顶层 `cli_tools` 数组。
- **旧 artifact 必须继续可用**：没有 `cli_tools` 字段 = 没有 manifest 下发的
  CLI 工具，等价于 `[]`。
- **新引擎读旧 artifact**（`schema_version: 4`）：按无 `cli_tools` 处理。
- **旧引擎读新 artifact**：平台会保证在你们支持 v5 之前不下发 `cli_tools`，
  所以不会出现这种情况；但按契约的一般原则，未知字段应被忽略而非报错。

---

## 7. 验收清单

供实现完成后自查，也是平台侧联调时会验证的点：

- [ ] `entrypoints` 列出的每个命令，agent 进程可按名字直接调用。
- [ ] 两个工具的 entrypoint basename 相同时**不会**出现「其中一个静默遮住另一个」
      ——平台已在 `PUT` 拒绝（§3.3.2），所以这种 artifact 不应到达你们；若真的到达，
      报错优于任选一个。
- [ ] **只有 `entrypoints` 列出的路径被置为可执行**，包内其余文件不是。
- [ ] 一个规范化后逃出 `path` 子树的 `entrypoints`（若真的到达了引擎）**被拒绝**，
      而不是被 chmod 或建链——平台侧已经拦掉了，这是兜底（§3.3.1）。
- [ ] `entrypoints` 之外的包内文件按原相对结构落地（`bin/tk` 能找到 `lib/`）。
- [ ] 同名工具换版本后，容器内是新版本，无旧版本残留。
- [ ] 上一版有、这一版没有的工具，命令不再可调用。
- [ ] `cli_tools: []` 清空所有 manifest 下发的工具，且**不影响**镜像自带命令。
- [ ] 重复投递同一 artifact 收敛，无副作用堆积。
- [ ] 无 `cli_tools` 字段的旧 artifact 行为不变。

---

## 8. 需要你们反馈的

1. **落点与 PATH 的做法**——你们打算怎么实现（独立目录 + PATH 追加？软链到
   已有 PATH 目录？），我们记进能力矩阵，便于排查问题时两边对齐。
2. **沙箱策略**——用户提供的二进制在你们容器内是否有权限限制、是否需要额外
   声明。如果有约束，越早说越好，因为它会影响 manifest schema 要不要暴露相关
   字段。
3. **是否接受 `schema_version` 5**，以及你们支持它的时间点——平台会在此之前
   不下发 `cli_tools`。

---

## 附：相关文档

| 文档 | 内容 |
| --- | --- |
| `design.zh-CN.md` | Bot Config Manifest 总体设计 |
| `manifest-schema.zh-CN.md` §3.7 | 用户侧 `cli_tools` 的 schema（本文档是它的引擎侧对应） |
| `engine-requirements.zh-CN.md` | 引擎侧要求全集 |
| `work-items.md` W9 / W12 | 实现工作项与跨引擎语义契约 |
| `kernel/bot_config/artifact.py` | `BotConfigArtifact` 的代码定义（契约的事实来源） |
