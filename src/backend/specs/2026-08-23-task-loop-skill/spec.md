# spec · task-loop 预装 skill 整合

## 背景

现有 **5 个 skill** 构成"任务目标驱动执行闭环"，分属不同执行 bot / runtime，各自有触发词与 I/O 契约：

| 段 | skill | 运行 bot | 触发 | 输出 |
|---|---|---|---|---|
| 任务识别 | task-recognition（~/.Desktop/task-recognition） | 对话 bot（用户面） | `/task` 或平台 `[RESUME_TASK]` 注入（执行**之前**） | AixUI 卡片（task_clarify / task_multi_select / task_ready）→ 平台层调 `POST /tasks/execute` |
| 任务规划 | task-planning（Avernet singlebox_e2e/skills/planning） | owner bot | 框架 prompt 头部 `[planning]` | JSON `List[TaskSpec]` + has_gap；`[]` 即 gap 闭=验收通过 |
| 任务派发搜推 | task-search（…/skills/search） | owner bot | 框架 prompt 头部 `[search]` | 4 态 JSON（HIT_SINGLE/HIT_GROUP/HIT_MULTI_BOTS/MISS） |
| 任务验收 | task-acceptance（…/skills/acceptance） | worker bot | 叶子 execute 后自调 | 折叠进回投 result（success/data/gaps） |
| BBS 接力 | bbs-relay-single-task（Avernet specs/2026-08-09-…/bbs-relay-single-task） | 中继 bot | 引擎主动通知（含 task_id+backend+bot_id） | dashboard→attach→执行→result |
| 任务规划·arch 场景 | planning-arch（Avernet singlebox_e2e/skills/planning-arch） | owner bot | 框架 `[planning]` + arch 场景(prompt 含「某某某公司」) | JSON `List[TaskSpec]`+has_gap;确定式按根验收交付物集合 + done_children 查表(N_tech_stack/N_dual_view/N_architects) |
| 架构师名册 mock | arch-analysis（Avernet singlebox_e2e/skills/arch-analysis） | 中继/worker bot | 叶子 instruction 含「某某某公司」 | mock 伪造架构师名册 JSON(domain/architects[]/note) |

这些 skill 当前各自打包上传（`/api/skills/upload`）并按角色安装（planning+search→owner、acceptance→worker），案例知识只在 skill 里、框架零 case 知识。task-recognition 自身提到"执行手段编排归 orchestrate skill"，但该 orchestrate skill 目前缺失。

## 问题

- 5 个 skill 分散在 Desktop / Avernet 两仓库，没有单一真源的整合视图。
- 安装需按 bot 角色分别分发，运维负担与配错风险（错装/漏装一段，行为就缺）。
- 闭环的全局路由 / 交接无统一说明（orchestrate skill 缺失）。

## 目标

做一个**任务目标驱动的 task-loop 预装 skill 包**：装到任一 bot 上，效果等同把 7 段各自装到它该装的 bot——同一份 skill 内容里含 7 段(5 段通用闭环 + arch 场景规划变体段7 + 架构师名册 mock 段6)，每段由**各自触发词自门控**，在某个 bot 上只会命中其中一段执行。

- 给**所有 bot 预装同一份** task-loop skill，"预装即齐备"。
- 每段行为与"单独安装该段"**完全等价**，不引入行为漂移。
- **两层结构保泛化**:段1~段5 为泛化主干(默认、源逐字节不可改、可处理任意任务);段6/段7 为演示案例叠加段,由案例信号门控、可插拔、零侵入主干。扩展契约:新 case = 追加其叠加段 + 注册案例信号(强特征),不改段1~段5;移除全部叠加段即回退纯泛化 task-loop。
- **task-recognition 段的逻辑零改**：其规则、卡片类型、`cardId='card_3e31e1f1'`、四要素追问、多任务、`[RESUME_TASK]`/`[待处理任务]`、路径优先级、副屏静默、workflow_id 兼容、执行按钮后平台接续等，原样保留。

## 为什么

- 简化分发：一个包预装所有 bot，替代按角色分包分发。
- 单一真源：一份 skill 即闭环全貌（各段契约与交接），降低对齐与回归成本。
- 等价保证：每段原文不动地嵌入 + 顶层路由按触发词分流，既不破坏任何段既有行为，又能统一装载。

## 范围

**In-scope**
- 新建 `task-loop` skill 包（`SKILL.md` + `references/` + `README.md`）。
- 整合 7 段进同一 `SKILL.md`，按触发词分段自门控(段7 为 arch 场景规划变体,与段2 按「某某某公司」信号互斥分流;段6 为 arch 架构师名册 mock 叶子,段5 命中时按 bridge 并用段6 产出)。
- 顶层**路由规则**（新增的编排粘合层）：每个触发/上下文 → 命中哪一段，含优先级与"未命中即静默 no-op"。
- 携带各段依赖的参考文档（bbs: task-api/judge-rubric/idempotency；recognition: 卡片数据格式/平台层接口协议）。
- 校验：frontmatter 可被解析；各段段体与 `demote(strip(源))` 逐字节一致(仅标题统一降一级,正文/HR/卡片零改);顶部 frontmatter 仅一组 `---`(2 个),recognition 段保留源 `---` 横线(6 个)原样,arch-analysis/planning-arch 段体无 `---` HR;SkillParser 只解析顶部 frontmatter 不受影响;再生成说明。

**Out-of-scope**
- 不重写/精简任何一段内部逻辑（recognition 受"零改"硬约束；其余段以"原文嵌入、行为不变"为原则避免漂移）。
- 不改 Avernet 框架代码、不改 5 段各自的源文件（它们仍是各段真源）。
- 不改 bbs 后端接口、不改任务协作中心接口。
- 不替换 Avernet e2e 用的 per-phase skill 包（planning/search/acceptance 仍是其 e2e 测试真源）。

## 需求

**Functional**
- F1 预装等价：任一 bot 装上 task-loop 后，给定其上触发的某段触发词，行为 == 该 bot 单独装了对应段 skill 的行为。
- F2 路由：顶层路由按"本次触发词/上下文"分流到且仅到一段；多 cue 并存按既定优先级；无 cue 命中时静默（no-op，不虚构任务、不乱执行、不追问）。arch 场景:`[planning]`+prompt 含「某某某公司」命中段7(优先于段2);段5 BBS 接力命中时 attach 的 scoped 叶子若 instruction 含「某某某公司」则并用段6 产出(arch 接力链路唯一例外)。
- F3 段独立性：各段触发条件、I/O 契约、环境约束（禁止联网等）、确定式剧本与源 skill 一致。
- F4 recognition 段零改：该段段体与 `demote(strip(~/Desktop/task-recognition/SKILL.md))` 逐字节一致(仅标题降一级;正文/HR/卡片零改,含 `cardId='card_3e31e1f1'`、四要素追问、多任务、`[RESUME_TASK]`/`[待处理任务]`、副屏静默、workflow_id 兼容、执行后平台接续)。

- F5 arch 场景规划变体(planning-arch):`[planning]`+prompt 含「某某某公司」命中段7,输出契约同段2,按根验收交付物集合 + done_children 确定式查表产 N_tech_stack/N_dual_view/N_architects;段7 段体与源 planning-arch SKILL.md 逐字节一致(仅标题降一级)。
- F6 架构师名册 mock(arch-analysis):叶子 instruction 含「某某某公司」命中段6,不联网返 mock 名册;段5 接力 attach 该叶子时按段6 产出;段6 段体与源 arch-analysis SKILL.md 逐字节一致(仅标题降一级)。

**Non-functional**
- N1 可装载：符合框架 `SkillParser` 对 frontmatter 的要求（name/version/description/author/tags），且 `description` 为单行(禁用 `|`/`>` 块标量,符合 Skill 规范 CSC002)。顶部 frontmatter 仅一组 `---`；recognition 段保留源 `---` 横线(6 个)原样(正文零改),SkillParser 只解析顶部 frontmatter 不受段内 `---` 影响。
- N2 单包多段：单 `SKILL.md` 承载 5 段；段体原文嵌入，顶层路由为唯一新增逻辑。
- N3 可再生：`SKILL.md` 由 5 个源 SKILL.md + 路由模板拼装；附再生成步骤，源更新时按相同规则再生成。
- N4 无行为漂移：除顶层路由外不动各段内部。
- N5 双变体 + 部署清洗:`assemble.py` 常量 `DEPLOY_GATEWAY` 控制变体——置预发网关则生成预发部署包(recognition 段本地联调 `http://localhost:8888` → `https://teamclawgw-pre.alipay.com`,`本地联调`注释→`预发`),置 None 则保留源 localhost(本地 e2e 变体);源 SKILL.md 不动,两变体可再生。recognition 段体除该 URL 重定向外逐字节不变(零改硬约束的唯一下沉豁免)。

## 成功标准

- 装到 4 类示例 bot（chat/owner/worker/relay），各自用各自触发词触发(含 arch 场景:owner 段7 规划→N_architects MISS 升 BBS;中继 bot 段5 接力 + 段6 mock 名册)，输出与"单独装该段"一致（recognition 出对应 AixUI 卡片；planning 出 `List[TaskSpec]`+has_gap；search 出 4 态 JSON；acceptance 折叠进 result；bbs 走 attach→result）。
- 各段段体与 `demote(strip(源))` 逐字节一致(脚本校验;recognition 成功覆盖零改硬约束)。
- 合并 SKILL.md 通过 frontmatter 解析且无嵌套 `---`。
- 7 个触发词各命中唯一一段、无 cue 时静默 no-op(段5↔段6 arch 接力并用为唯一例外)。

## Open questions（gate 待确认）

- Q1 位置：✅ 已确认——skill 包统一放 `Avernet/src/backend/specs/2026-08-23-task-loop-skill/task-loop/`，与 spec/plan/tasks 同 feature dir 统一管理。
- Q2 包名/版本：`name=task-loop`, `version=1.0.0`, `tags=[task,loop,orchestrate,...,arch-analysis,task-planning-arch]`？
- Q3 顶层路由是否需要"段不命中也输出/报错"，还是静默 no-op？（草案：静默 no-op）
- Q4 是否需要保留可直接从源再生成 SKILL.md 的拼装脚本？（草案：要，纳入 tasks）

## Decisions（单方面假设，gate 复核）

- D1 位置：feature dir = `/Users/shangjian.msj/Github/Avernet/src/backend/specs/2026-08-23-task-loop-skill/`（含 `spec.md`/`plan.md`/`tasks.md` + `task-loop/` 可上传 skill 包 + `assemble.sh`），统一管理。
- D2 整合形态：单包多段、触发词自门控（方案 2 的可装载变体，非纯文档）。
- D3 各段原文嵌入（剥离各自 frontmatter 后拼入），仅新增顶层路由；不精简、不改写。
- D4 `references/` 携带 bbs 三份参考 + recognition 两份支撑文档。
- D5 源真源不变：recognition→`Desktop/task-recognition/SKILL.md`；planning/search/acceptance/arch-analysis/planning-arch→`Avernet singlebox_e2e/skills/`；bbs→`Avernet specs/.../bbs-relay-single-task/SKILL.md + references/`。
- D6 场景叠加层:泛化主干(段1~段5,默认不可改)+ 案例叠加段(段6/段7,案例信号门控、可插拔、零侵入)。泛化性由主干保证(默认路径),针对性优化由叠加段提供;新 case 遵扩展契约,不动主干。
