# 自定义 Stage 开发记录与上传协议

- 用户确认流程、Stage 和开放位置后，`POST /api/evolve/stage-developments` 创建独立开发记录。记录归当前用户所有，此时不创建实现版本或集成测试任务。
- `GET /api/evolve/stage-developments` 列出记录；按记录 ID 读取可恢复同一开发过程。无上传版本的记录可删除。
- 开发包通过 `GET /api/evolve/stage-skills/developer-package?developmentId=...` 按已保存的选择生成，只包含 `SKILL.md`，按所选流程、Stage 和接入方式生成四节说明：背景与本次开发目标、输入与输出、平台提供的能力、开发与交付。输入输出以业务 JSON 示例和字段要求内嵌展示，不再附带 `contract.json`；平台内部保留 Schema 校验。
- 上传请求携带开发记录的 `stageSkillId`。平台核对归属及 Stage/位置，不接受由上传文件声明身份。ZIP 只需包含完整 Skill：根目录 `SKILL.md`，或唯一一层包装目录中的 `SKILL.md`；无需 `stage-skill.json`，无需固定 `implementation/` 目录。
- 每次成功上传生成独立、不可变的实现版本，保存 ZIP 校验和、平台识别的入口和静态校验结果。集成测试绑定精确 `implementationId`，切换查看版本不会启动测试或复用另一版本的测试结论。
- Stage Input 的 `implementation.entrypoint` 来自该版本保存的校验记录。历史包保持不变；旧记录缺少入口元数据时使用其原有 `implementation/SKILL.md` 约定。

新上传且开发记录为 `skill_evolution + plan + replace` 的版本，在平台静态校验记录中冻结 `executionContract: clawevolve.plan-business/v1`，表示只提供默认 Plan 流程内的 Agent 业务能力。此字段由平台按已归属的开发记录设置，不读取上传表单或 ZIP 自行声明的执行版本。已有版本没有该字段时仍保持旧执行协议；不在读取时推断或迁移。新协议必须由配套运行入口、真实 Plan Source 和独立目标工作区一起满足后才允许执行，不能因缺少输入而回退成旧整段处理或默认 Agent。

### 规划业务协议的来源和交互

默认 Diagnose 成功报告时，可以在报告顶层携带实际生成的 `planSource`（`plan-source/v2` 完整文档），而不是在业务 `output` 中自行声明冻结来源。平台核对本次 Task、Bot 和案例身份，使用共享契约计算规范化摘要，保存带 producer 身份的内联描述符。空案例诊断不提供来源；历史终态任务不补登记来源。

新规划业务协议的独立集成测试必须提交 `diagnoseSourceRef: { taskId, stepId, digest }`，引用同 owner、同 Bot、已完成任务中成功的默认 Diagnose Step。`digest` 包含 `sha256:` 前缀。平台解析冻结来源并生成 `diagnose_result`，不接受同时手工提供该字段，不创建伪造的上游 Step。正式完整任务直接消费本任务真实 Diagnose 的冻结来源。

Optimize 独立集成测试可提交 `planSourceRef: { taskId, stepId }`，引用同 owner、同 Bot 已完成任务中的成功默认 Plan 或有运行记录的 `plan/replace` Step。平台校验完整 Plan 输出及已发布模板的 owner/domain/version，将完整输出与来源身份冻结为 `config.stageTest.planResultSource`。不能同时提交 `caseInput.plan_result`，不能引用历史手填的 `stage-test supplied Plan result` Step。此路径只创建本次实际执行的 Optimize 及后置步骤，不制造成功 Plan Step；默认 Optimize 的 `inputs.diagnoses[].plan` 和后置的 `input.plan_result` 均使用冻结的真实来源，后置的 `builtin_result` 仍取本轮真实 Optimize 输出。旧测试输入路径保持兼容。

Stage Input 为新协议额外提供 `implementation.executionContract`、`task.flow: skill_evolution`、`task.ownerUserId`、`inputs.planSource` 和 `runtime.plan.model`。四次业务调用（discovery、case_contract、objective_document、spec_document）由默认 Plan 执行器管理；拆分、模板发布和最终完整 Plan 结果仍由平台生成。开发包只描述对应业务输入输出，不能要求开发者填写发布后的模板 ID。

完整 Skill 任务的默认 Optimize Input 使用实际的逻辑 Plan 生产者：同 Task 仅选择当前 Optimize 之前成功的默认 Plan，或有准确 Task/run、Stage/mode、实现 owner 归属的 `plan/replace` / `plan/postprocess` Step。保留后置优先于替换、替换优先于默认的既有逻辑结果顺序；前置、失败、未来 Step 和无合法运行记录的扩展不能作为 Plan 输出。`inputs.diagnoses[].plan` 返回真实 `{stepId, output}`，不另造成功默认 Plan Step；Optimize 后置的 `plan_result` 使用同一解析规则。跨来源 Task 仍需同 owner/Bot，显式 StageTest `planSourceRef` 的完整冻结分支优先且保持不变。

该修复仅补齐控制面输出交接，不宣称 full 的模型/文件流程已验收。正式候选仍由 `targetSkill.candidate.prepared` 提供同一 workspace；Plan 原文产物和 Optimize 读取之间的受控运行时 results 链接需要实际验收，不能由独立 StageTest 的跨 Task handoff 测试代替。

新协议请求用户补充信息时，报告的 `progress.business_resume` 必须包含运行时产生的 `request_id` 和 `question_sha256`（64 位小写 hex）。平台随该问题冻结这两个不透明标识，不消费本地 state 路径；用户回答原样保存为文本或表单对象。恢复输入通过 `runtime.plan.hitl_response: { request_id, question_sha256, answer }` 返回同一问题的标识和原始答案，绝不从用户字段提取标识。再次提问使用新的 request ID。旧执行协议的 `human_input` 交互保持不变。

兼容范围：旧版本仍可查看、测试和被已冻结任务执行；旧实现系列可继续升级。新上传必须关联已有开发记录或历史实现系列。安全检查保留 ZIP 越界/软链/体积限制、入口非空和未开发说明检查；运行时输入与平台传输协议保持兼容。业务 Skill 可直接写入业务结果 JSON，或 `{ question: { tag, format, content } }`；平台运行入口补齐传输封装。已有实现的显式 HITL 封装继续受支持，不能重复包装。开发者只需开发并打包，提示用户上传；初始化、结果上报与调度由平台入口负责。

## Stage 集成测试边界

`POST /api/evolve/stage-skills/:implementationId/integration-tests` 接收测试 Bot、环境和当前 Stage 的 `caseInput`，不接收 `targetSkillAssetId`，也不允许通过 `caseInput.target_skill` 注入待进化 Skill 路径。旧客户端提交这些字段会返回 400，且不创建测试或访问 OCB Skill。正式 Skill 自进化任务入口保留目标 Skill 的选择和完整生命周期。

测试绑定准确实现版本，只运行当前 Stage：前置接平台默认处理、默认处理接后置，或整体替换。平台补齐任务元数据、默认 Session 来源及交互回答，并通过现有运行通道下发；不切换自进化流程，也不确认或应用 Skill 版本。Skill 流程的 Optimize 测试在业务 Stage 前执行下述隔离准备，不创建 `skill_finalize`。诊断目标不是 Session 数据，Session 仍由执行 Bot 读取；本次不新增用户上传资源或填写容器路径的入口。

### Skill Optimize 的隔离准备

新建 `skill_evolution + optimize` 测试必须使用真实 `planSourceRef`，并从成功 Plan 独立测试取得当时的固定 Skill 资源。不能根据 Plan 文本猜测 Skill、导出当前安装源替代当时的目标，也不能把完整任务后来生成的最终候选包当成 Plan 分析时的快照；没有可验证的 Plan 目标快照时拒绝。来源身份、资源路径及 SHA 不合法时，在建 Task、写资源或派发前失败；手填 Plan 仍仅兼容非此 Skill 流程。

平台将目标包身份与完整 Plan 输出一起冻结，为本次测试设置 `fixture:{taskId}` 命名空间的 `targetSkill`，不创建 OCB 资产。首个真实 Step 是现有 `skill_prepare`：下载并校验源包，准备本 Task 的独立 workspace。准备报告必须匹配本 Task 的候选路径；成功后才派发默认 Optimize/相应前置，默认 Optimize 的 `--workspace` 和扩展的 `input.target_skill` 均取同一已准备副本。Tune/Review 使用既有候选文件系统边界，禁止回落到 Bot 安装源。测试完成仍按 StageTest 结束，不确认、应用或登记正式 Skill 版本。

此变更影响新 Skill Optimize 测试的创建、准备及默认/扩展步骤输入；普通 Bot 测试保持原入口。旧任务、来源输出、模板和评分不重写。缺少隔离配置的历史失败任务不能直接重试成新验收，必须修复后重新创建测试。控制面回归只证明编排与输入契约，真实文件隔离及模型行为另行验收。

历史测试的配置、状态、版本关联、答案和产物不迁移或删除。页面将历史准备、打包及已提供的上游输入折叠为“测试环境准备与产物记录”，交互归属原 Step。已回答交互显示问题和答案，原始 JSON 仅在技术详情中展示，不再渲染可提交的空表单。需要原目标候选的历史专项用例不能靠重新传目标资产启动；不能把仅移除绑定说成新增了通用文件准备能力。

### 固定测试 Skill 资源（显式版本）

本功能只给关联开发记录的 `flow_key=skill_evolution` 且接入位置为 `diagnose/preprocess` 或 `plan/replace` 的新独立测试提供固定输入。选择依据是 `implementation.stage_skill_id` 对应开发记录的 owner、Stage、mode、flow，不看中文名称、请求中的 flow 或上传包内声明。Bot 流程、没有开发记录的历史实现及其他组合保持不注入；不回填旧任务。

`stage-test-fixture.ts` 从 `server/resources/evolve/stage-test-fixtures/skill-description-v1.json` 的固定正文生成真实 ZIP。JSON 是供现有 tsc 构建携带资源的源码格式，交给运行时的 ZIP 仅含根 `SKILL.md` 一个普通文件，无目录项：frontmatter name 为 `stage-test-text-summary`，正文为中文文本摘要整理的用途、输入、步骤、输出，包含可依据原文明确的指代；不包含预计算结果或成功标记。固定 UTC 日期 `1980-01-01`、UNIX `100644`、DEFLATE level 6，v1 ZIP 为 514 字节、原文 634 字节，SHA256 为 `ba510d33b234af46de86eb5eeef3a581775314cc33f69f2d380a3c3d88e9e17d`。改变正文或打包规则必须作为显式版本变更，不在旧测试中重新生成。

诊断前置和旧 JSON 规划替换协议的新测试使用 `skill-description-v2`（`version: 2`），正文来源为同目录 `skill-description-v2.json`：第 2 步仅写“按它整理要点”，保留明确的说明缺口，供说明加固能力实际检查并决定是否修改；不包含修复答案、强制修改要求或预计算结果。v1 正文、ZIP 和历史冻结身份保持不变。版本只在创建新 Task 时选定，读取、恢复及重试不能升级现有 Task 的资源。

只有新的 `skill_evolution + plan/replace + clawevolve.plan-business/v1` 测试使用 `skill-description-v3`（`version: 3`）。它是固定的 `daily-report-zh` 原文副本，不是按业务相似性选择通用摘要 Skill。来源为本轮提供的 `packages/daily-report-zh/SKILL.md`，与当前验收 Bot 原始 Skill 及登记资产 baseline ZIP 内的 `SKILL.md` 逐字节相等：1979 字节，文件 SHA256 `eb866600542fc91fd5851d1b5d70a88e7d180a6b779608afb51d36323bbe8459`。源码以 `skill-description-v3.json` 携带原文，不在运行时读取验收机器路径。相同固定打包规则生成仅根 `SKILL.md` 的 1233 字节 ZIP，ZIP SHA256 `78f8b6f83aceb7869586e69feaef40fd3249b6ed9f7ccd338f83670e50349164`；它不同于原登记资产（另含 `contract.json`）的 ZIP hash。

v3 的 `name` 为 `daily-report-zh`，`path` 为 `{workspace}/skills/skills-local/daily-report-zh`；`fixture_id`、`asset_id`、`skill_id` 分别为 `skill-description-v3`、`fixture:{taskId}:skill-description-v3`、`fixture:skill-description-v3`。`baseline_sha256` 仍是固定 fixture ZIP hash，不冒充 OCB 资产身份。v1/v2 的名称、路径、正文和已冻结任务完全不变。

该新协议必须引用合法的冻结 Diagnose Source。创建时逐个检查 `cases[].query` 明确以独立 `/daily-report-zh` 命令开头；若包含 `context.original_query`，也必须满足同一身份，出现其他独立 slash 命令则保守拒绝。无显式调用、名称前缀冒充、混合其他 Skill 或原始请求不一致均返回 400，发生在存包、建 Task 和 dispatch 之前。门禁不改写 Source 的 query、evidence、digest 或输出，不把历史 assistant 动作当需求，也不解析自然语言来推断适配。无法证实只报告资源不匹配；不自动寻找资产或替换真实案例。旧 JSON 协议保持 v2，新 v3 不用于诊断前置。

创建测试时，平台将 ZIP 写入 `evolve/stage-tests/{taskId}/fixtures/{fixtureId}/package.zip`，成功后才创建 Task/派发 Step。`config.stageTest.fixture` 保存 `{kind, fixtureId, version, taskId, sha256, ref}`；不保存 signed URL，不写 `config.targetSkill` 或 `config.flow`。存储失败明确失败，不伪装成测试已开始或已通过。测试不登记 Skill 资产、不生成正式版本、不新增 Skill 审计事件，也不调用 OCB export/replace。

内部 Step Input 的平台资源协议如下（示例为历史 v1；新任务按上述协议选择 v2 或 v3，标识和版本必须成对匹配；`sha256` 是无前缀的 64 位小写 hex）：

```json
{
  "resources": {
    "testSkillFixture": {
      "kind": "stage_test_fixture",
      "fixtureId": "skill-description-v1",
      "version": 1,
      "taskId": "<本次Task>",
      "sha256": "<固定ZIP SHA256>",
      "package": { "method": "GET", "url": "<平台签名URL>" }
    }
  }
}
```

外层 `task` 仍为 `{taskId, taskType, targetBotId}`，业务 `input.task` 仍为 `{task_id, task_type, target_bot_id}`，两者描述同一个 `stage_test` 和 Bot。控制面提供业务 `input.target_skill`：

- `kind: stage_test_fixture`、`fixture_id: skill-description-v1`；
- `asset_id: fixture:{taskId}:skill-description-v1`、`skill_id: fixture:skill-description-v1`，均为明确的测试资源命名空间，不是登记资产或 OCB Local Skill 身份；
- `name: stage-test-text-summary`；
- `workspace: /home/admin/.openclaw/clawevolve_workspaces/{taskId}/workspace`；
- `path: {workspace}/skills/skills-local/stage-test-text-summary`；
- `baseline_sha256` 等于资源 ZIP 的 SHA256，不声称是从 OCB 导出的原包。

这些是平台逻辑路径，不是已经准备完成的证明；运行时映射到自身 runtime layout，下载、校验并隔离落盘后才交给业务 Skill。fixture 的压缩和展开各限 1 MiB，只允许 UTF-8 根 `SKILL.md` 普通文件。执行边界由 Lorentz 的运行时实现承担；控制面不创建正式 prepare/finalize Step、正式 candidate marker 或应用行为。

新请求在原有 `targetSkillAssetId`/`caseInput.target_skill` 拒绝规则之外，也拒绝顶层及 `caseInput` 中的 `resources`、`testSkillFixture`、`stageTest`。历史已保存的业务输入即使含这些字段，也只在交付副本中过滤；不改变历史原始 JSON。目标字段始终由平台冻结配置重新组装；签名只允许当前 Task 的固定对象 ref，不消费用户 URL、路径或查询参数。

读取、HITL 恢复和同 Task 的新 Step 重试只重新签发冻结 ref 的 GET URL；不重新生成/上传 ZIP，不更换 fixture hash/身份。运行时应保留副本上的真实改动，拒绝 marker/hash/目录边界不一致；新的集成测试产生新的 Task，因此取得新的独立副本。该恢复不代表重新执行已完成的业务处理：重复报告不得重复派发后续默认 Stage。

测试与真实验收必须区分：route 测试用协议报告验证编排，helper 测试验证真实 ZIP 和 hash；这些不证明模型已阅读或修改文件。缺少资源不能用 `changed:false` 成功收尾。`diagnose/preprocess` 的真实前置结果后仍运行默认 Diagnose，只有整个 Stage 完成才通过；默认 Diagnose 读取真实 Session，不因本功能自动消费修改后的 Skill 或证明效果提升。`plan/replace` 必须交付真实完整 Plan 结果。本次不扩展资源管理、UI、正式生命周期或历史审计。
