# plan · task-loop 预装 skill 整合

## 架构总览

单 `SKILL.md` 承载 7 段(段1~段5 泛化主干 + 段6/段7 案例叠加层) + 顶层路由 + 场景叠加层契约；`references/` 携带参考文档；预装到所有 bot。模型收到某触发词时，按顶层路由**只跑命中的一段**，其余段不参与。

```
/Users/shangjian.msj/Github/Avernet/src/backend/specs/2026-08-23-task-loop-skill/task-loop/
├── SKILL.md
│   ├─ frontmatter (name=task-loop …)
│   ├─ # task-loop + 总述（预装等价 / 5 段 / 只跑命中段 / 不并段）
│   ├─ ## 路由规则（新增编排粘合，最先读）
│   ├─ ## 段1 任务识别（recognition，原文嵌入，零改）
│   ├─ ## 段2 任务规划（planning，原文嵌入）
│   ├─ ## 段3 任务派发搜推（search，原文嵌入）
│   ├─ ## 段4 任务验收（acceptance，原文嵌入）
│   ├─ ## 段5 BBS 接力（bbs-relay，原文嵌入;references 随包携带,段体无内联引用）
│   ├─ ## 段6 架构师名册 mock（arch-analysis，原文嵌入）
│   └─ ## 段7 任务规划·arch 场景（planning-arch，原文嵌入）
├── references/
│   ├─ bbs-task-api.md            ← bbs references/task-api.md
│   ├─ bbs-judge-rubric.md        ← bbs references/judge-rubric.md
│   ├─ bbs-idempotency.md         ← bbs references/idempotency.md
│   ├─ recognition-card-format.md    ← task-recognition/卡片数据格式.md
│   └─ recognition-platform-protocol.md ← task-recognition/平台层接口协议.md
└── README.md   (预装说明 + 从源再生成步骤 + 真源清单)
```

## 源真源（不改）

- recognition：`~/Desktop/task-recognition/SKILL.md`（最新；段体逐字节取自此）
- planning / search / acceptance：`Avernet/.../tests/community/core/task/singlebox_e2e/skills/{planning,search,acceptance}/SKILL.md`
- bbs：`Avernet/.../specs/2026-08-09-task-goal-driven-bbs-active-relay/bbs-relay-single-task/SKILL.md` + `references/`
- arch-analysis：`Avernet/.../tests/community/core/task/singlebox_e2e/skills/arch-analysis/SKILL.md`(mock 架构师名册叶子;无 references)
- planning-arch：`Avernet/.../tests/community/core/task/singlebox_e2e/skills/planning-arch/SKILL.md`(arch 场景规划变体;无 references)

## SKILL.md 拼装规则

1. **frontmatter**：`name: task-loop`、`version: 1.0.0`、`author`、`tags: [task, loop, orchestrate]`、`description`（说明预装等价 + 5 段 + 触发词自门控）。**description 必须单行**(禁用 YAML `|`/`>` 块标量,符合 Skill 规范 CSC002 `description_no_newline`)。
2. **总述**：预装即齐备、5 段、只跑命中段、不并段。
3. **路由规则**（新写，唯一新增逻辑）：见下。
4. **嵌 7 段**：依次剥离源 SKILL.md 的 frontmatter(ORDER = recognition, planning, search, acceptance, bbs, arch_analysis, planning_arch)（顶部首个 `--- … ---`），取其**正文**作为对应 `## 段N` 下的内容；源正文标题统一降一级(H1→H2、`##`→`###` …),以维持单顶级 `# task-loop`;正文文本与 `---` 横线零改。
   - recognition 段：正文不加修改（含 `cardId='card_3e31e1f1'`、四要素追问、多任务、`[RESUME_TASK]`/`[待处理任务]`、副屏静默、workflow_id 兼容、执行后平台接续）。
   - planning / search / acceptance：正文不加修改（保留禁止联网、`[planning]`/`[search]` 触发、JSON 契约、确定式剧本、bot_id 取自 catalog）。
   - bbs：正文不加修改；其内部对 `references/` 的指向改指 `task-loop/references/{bbs-task-api.md,…}`。
5. **references/**：拷 bbs 三份 + recognition 两份，文件名加 `bbs-`/`recognition-` 前缀防同名。

## 路由规则设计（新增粘合）

按**本次收到的触发词/上下文**分流（不按 bot 身份）：

| 触发 / 上下文 | 命中段 |
|---|---|
| 用户消息以 `/task` 开头 / 上下文含 `[RESUME_TASK]` / 仅 `<AixUI … component="task-loop">` 副屏标签 | 段1 recognition |
| prompt 头部 `[planning]` + 目标节点 `node_id` + 任务态快照 | 段2 planning |
| prompt 头部 `[search]` + 子任务需求 + 候选集 `catalog` | 段3 search |
| worker 执行完叶子任务需自验收（框架 `format_execute` 投递 goal/instruction/sibling_outputs/execute_output） | 段4 acceptance |
| 引擎主动发的 BBS 任务消息（含 task_id + backend base url + 自身 bot_id，引擎已占根） | 段5 bbs-relay |
| `[planning]` + prompt 含「某某某公司」(arch 场景) | 段7 planning-arch(优先于段2) |
| 叶子 instruction 含「某某某公司」(非框架 `[planning]`/`[search]` 头) | 段6 arch-analysis |

**优先级**（实际多互斥，显式定义）：段5(引擎 BBS 通知) > 段2/段3(框架 `[planning]`/`[search]` 头) > 段4(叶子自验收) > 段1(`/task`/`[RESUME_TASK]`)。

**段5 ↔ 段6 并用(arch 接力链路唯一例外)**:段5 命中(BBS 通知)时,其 attach 的 scoped 叶子若 instruction 含「某某某公司」,叶子产出按段6 arch-analysis(mock 名册),段5 仍管 attach/result 协议;其余严格只跑命中段。

**未命中**：静默 no-op——不虚构任务、不乱执行、不追问、不输出卡片。

路由规则文本须显式声明"只跑命中段，其余段不参与"，并给反例（如把 `[planning]` 误当任务识别），防模型误并段。

## 包装 / 解析约束

- frontmatter 合规：`SkillParser.parse_content` 能解析（name/version/description/author/tags 非空）。
- 段体嵌入前**剥离源 frontmatter**，确保 SKILL.md 内顶部 frontmatter 仅一组 `---`(2 个);code-fence 内外的 `---` 不混入 frontmatter。recognition 段保留源 `---` 横线(6 个,全文共 8 个 `---`:2 frontmatter + 6 HR)原样,SkillParser 只解析顶部 frontmatter 不受影响。
- 段标题降级（源 `##` → `###`），维持单一顶级 `# task-loop`。

## 验证

- 逐字节：`cmp` recognition 嵌入段正文 vs 源 SKILL.md 正文（剥离 frontmatter 后）一致。
- 解析：用 `SkillParser.parse_content` 校验 frontmatter（name=task-loop/version/tags/description）。
- grep：SKILL.md 内 `^---$` 仅出现于顶部 frontmatter（2 次，一组）。
- 路由 walk：5 触发词各命中唯一一段；空 cue 命中 no-op。

## 风险

- R1 路由误判（模型把 `[planning]` 当任务识别）：路由规则放最前、措辞强、加反例与"仅命中段"硬约束。
- R2 行为漂移（手动改写各段）：原文嵌入 + 再生成脚本 + 逐字节校验。
- R3 源更新不同步：README 注明从源再生成步骤，pin 源版本。
- R5 arch 场景与通用段触发重叠(`[planning]` 段2 vs 段7):用「某某某公司」信号互斥分流 + 路由表显式标注;段5↔段6 并用为唯一例外,显式声明避免误并段。
- R6 泛化 vs 案例优化:两层结构(泛化主干段1~段5 不可改 + 案例叠加段信号门控可插拔)保证默认纯泛化、案例优化零侵入主干;新 case 遵扩展契约(SKILL.md `## 场景叠加层` 子节固化)。
- R4 单包体量（5 段约 15k tokens）：可接受（skill 为静态文本）；references 按需懒读。

## 再生成（README 中固化）

提供 `assemble.sh`（内部调用同目录 `assemble.py`），按上述拼装规则从 5 源 + 路由模板重新生成 `SKILL.md` 与 `references/`，确保源更新后可一键对齐。

## 部署期清洗(本地 e2e vs 预发双变体)

`assemble.py` 常量 `DEPLOY_GATEWAY` 控制变体:置 `https://teamclawgw-pre.alipay.com` → 生成预发部署包(本地联调 `http://localhost:8888` → 预发网关;`本地联调`注释→`预发`);置 `None` → 保留源 localhost(本地 e2e 变体)。源 SKILL.md 不动,两变体均可一键再生;校验脚本按 `deploy_strip(expected)` 比对。recognition 段体除该 URL 重定向外逐字节不变(零改硬约束的唯一下沉豁免)。`assemble.py` 暴露 `strip_frontmatter`/`demote_one`/`body` 与源路径常量供校验脚本 import(顶部 `if __name__` 守卫,import 无副作用)。
