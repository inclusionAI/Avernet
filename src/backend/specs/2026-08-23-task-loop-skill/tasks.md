# tasks · task-loop 预装 skill 整合

> 验收以"预装等价 + recognition 段零改 + 路由命中正确"为主线。每组跑完再进下一组。

## G1 骨架与参考文档
- [x] 1.1 建 `/Users/shangjian.msj/Github/Avernet/src/backend/specs/2026-08-23-task-loop-skill/task-loop/{,references}`（`specs/2026-08-23-task-loop-skill/` 已由 SDD 产出）
- [x] 1.2 拷 bbs references → `references/{bbs-task-api.md, bbs-judge-rubric.md, bbs-idempotency.md}`
- [x] 1.3 拷 recognition 支撑文档 → `references/{recognition-card-format.md, recognition-platform-protocol.md}`
- [x] 1.4 校验：`references/` 5 文件存在且内容与源一致

## G2 顶层路由与 frontmatter（唯一新增逻辑）
- [x] 2.1 写 SKILL.md frontmatter（`name=task-loop`、`version=1.0.0`、`author`、`tags=[task,loop,orchestrate]`、`description` 单行说明预装等价+5段;禁用 `|`/`>` 块标量,过 CSC002）
- [x] 2.2 写 `# task-loop` 总述：预装即齐备 / 5 段 / 只跑命中段 / 不并段
- [x] 2.3 写 `## 路由规则`：7 触发词各→一段映射 + arch 场景 `[planning]` 互斥分流(段7 vs 段2) + 段5↔段6 arch 接力并用例外 + 优先级 + 未命中静默 no-op + "仅命中段"硬约束 + 反例
- [x] 2.4 校验：路由表覆盖 5 触发词各唯一命中、空 cue 命中 no-op

## G3 段1 任务识别（零改，硬约束）
- [x] 3.1 剥源 `~/Desktop/task-recognition/SKILL.md` frontmatter 取正文，嵌入为 `## 段1·任务识别`（标题 `##`→`###`）
- [x] 3.2 逐字节校验：嵌入段段体 == `demote(strip(源 SKILL.md))`(仅标题降一级;正文/HR/卡片零改)
- [x] 3.3 确认关键约束保留：`cardId='card_3e31e1f1'`、四要素追问、多任务、`[RESUME_TASK]`/`[待处理任务]`、副屏静默、workflow_id 兼容、执行后平台接续
- [x] 3.4 grep 反检：recognition 段内不含被改写的规则句

## G4 段2/段3 planning · search
- [x] 4.1 嵌 planning 段（源 Avernet `singlebox_e2e/skills/planning/SKILL.md` 正文）→ `## 段2·任务规划`
- [x] 4.2 嵌 search 段（源 `…/search/SKILL.md` 正文）→ `## 段3·任务派发搜推`
- [x] 4.3 保留：禁止联网、`[planning]`/`[search]` 触发、JSON 输出契约、确定式剧本、bot_id 取自 catalog
- [x] 4.4 校验：两段正文与源（剥 frontmatter）逐字节一致

## G5 段4 acceptance
- [x] 5.1 嵌 acceptance 段（源 `…/acceptance/SKILL.md` 正文）→ `## 段4·任务验收`
- [x] 5.2 保留：worker 自验收、折叠进 result（success bool / gaps 非空约束）、禁止联网、PASS/FAIL 链路、聚合/根验收归 planning（非本段）
- [x] 5.3 校验：段正文与源逐字节一致

## G6 段5 bbs-relay
- [x] 6.1 嵌 bbs 段（源 `specs/…/bbs-relay-single-task/SKILL.md` 正文）→ `## 段5·BBS 接力`
- [x] 6.2 把 bbs 段内对 `references/` 的指向改为 `task-loop/references/{bbs-task-api.md,bbs-judge-rubric.md,bbs-idempotency.md}`（实测 bbs 段体(marker 之后)无内联 references/ 或文档名引用,无需改写;三份参考文档随包以 references/ 形式携带,见 G1.2）
- [x] 6.3 保留：引擎通知触发、attach→执行→result、bot_id 用自身、backend base 从消息取、深度闸/幂等约束
- [x] 6.4 校验：段正文（除 references 指向外）与源逐字节一致

## G7a arch 场景两段(段6 arch-analysis / 段7 planning-arch) + 路由桥接
- [x] 7a.1 在 assemble.py 增源 `arch-analysis` / `planning-arch`(singlebox_e2e/skills 下),ORDER 追加为段6/段7
- [x] 7a.2 嵌 arch-analysis 段(源正文,标题降一级)→ `## 段6`;嵌 planning-arch 段 → `## 段7`
- [x] 7a.3 路由:段7=`[planning]`+prompt 含「某某某公司」(优先于段2);段6=叶子 instruction 含「某某某公司」;段5↔段6 arch 接力并用例外
- [x] 7a.4 逐字节校验:arch-analysis/planning-arch 段体 == `demote(strip(源))`(仅标题降一级);两段体无 `---` HR
- [x] 7a.5 frontmatter description 改 7 段单行(CSC002),tags 增 `arch-analysis`/`task-planning-arch`;README 改 7 段触发对照
- [x] 7a.6 场景叠加层契约:SKILL.md 增 `## 场景叠加层` 子节(泛化主干段1~段5 默认不可改 + 案例叠加段信号门控可插拔 + 扩展契约);spec/plan 同步两层结构与 D6/R6
- [x] 7b.1 部署期清洗:assemble.py 增 `DEPLOY_GATEWAY`+`deploy_strip`,预发包把 recognition 段 `http://localhost:8888` → `https://teamclawgw-pre.alipay.com`(+ `本地联调`注释→`预发`)(源不动,`DEPLOY_GATEWAY=None` 留本地变体);verify 按 `deploy_strip(expected)` 比对通过

## G7 包装校验、再生成脚本与 README
- [x] 7.1 校验：顶部 frontmatter 仅一组 `---`；recognition 段保留源 `---` 横线(全文 `grep -c '^---$'` = 8：2 frontmatter + 6 HR);SkillParser 只解析顶部 frontmatter 不受影响
- [x] 7.2 模拟 `SkillParser.parse_content` 解析 frontmatter 通过（name/version/tags/description 非空）
- [x] 7.3 写 `assemble.sh`：按拼装规则从 5 源 + 路由模板再生成 `SKILL.md` 与 `references/`
- [x] 7.4 跑 `assemble.sh` 产出与手写 SKILL.md 逐字节一致（再生性验证）
- [x] 7.5 写 README：预装到所有 bot 的说明 + 从源再生成步骤 + 真源清单 + 版本 pin
- [ ] 7.6 回归：装到 chat/owner/worker/relay 各触发一轮(含 arch 场景:owner 段7 规划→N_architects MISS 升 BBS;中继 bot 段5 接力 + 段6 mock 名册)，输出与"单独装该段"一致（recognition 卡片 / planning `List[TaskSpec]`+has_gap / search 4 态 / acceptance result / bbs result）；无 cue 时静默  > ⏳ 待环境验证：需 Avernet singlebox + 4 类 bot(chat/owner/worker/relay) 触发一轮做端到端回归,本会话无该环境,留待联调环境补验。除 G7.6 外静态校验(段体逐字节 / frontmatter / 路由 / references)均通过。
