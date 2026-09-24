# 自定义 Stage 开发记录与上传协议

- 用户确认流程、Stage 和开放位置后，`POST /api/evolve/stage-developments` 创建独立开发记录。记录归当前用户所有，此时不创建实现版本或集成测试任务。
- `GET /api/evolve/stage-developments` 列出记录；按记录 ID 读取可恢复同一开发过程。无上传版本的记录可删除。
- 开发包通过 `GET /api/evolve/stage-skills/developer-package?developmentId=...` 按已保存的选择生成，只包含 `SKILL.md`，按所选流程、Stage 和接入方式生成四节说明：背景与本次开发目标、输入与输出、平台提供的能力、开发与交付。输入输出以业务 JSON 示例和字段要求内嵌展示，不再附带 `contract.json`；平台内部保留 Schema 校验。
- 上传请求携带开发记录的 `stageSkillId`。平台核对归属及 Stage/位置，不接受由上传文件声明身份。ZIP 只需包含完整 Skill：根目录 `SKILL.md`，或唯一一层包装目录中的 `SKILL.md`；无需 `stage-skill.json`，无需固定 `implementation/` 目录。
- 每次成功上传生成独立、不可变的实现版本，保存 ZIP 校验和、平台识别的入口和静态校验结果。集成测试绑定精确 `implementationId`，切换查看版本不会启动测试或复用另一版本的测试结论。
- Stage Input 的 `implementation.entrypoint` 来自该版本保存的校验记录。历史包保持不变；旧记录缺少入口元数据时使用其原有 `implementation/SKILL.md` 约定。

## 执行协议

新上传且开发记录为 `skill_evolution + plan + replace` 的版本，由平台冻结 `executionContract: clawevolve.plan-business/v1`。业务 Skill 提供 Plan 内部的业务判断；原生 Plan 执行器负责调用、校验、文档渲染、模板发布与结果上报。已有版本按保存的执行协议运行，不在读取时迁移。

正常 Diagnose → Plan 通过执行工作区中的原生文件交接。Plan 自己负责来源解析与校验；CW 只接收 Stage 的业务结果，不额外收集、冻结或再次下发完整 Diagnose Plan Source。Insight 入口继续使用它原有的 Task Source 服务和协议。

业务交互可使用文本确认或表单。Plan 运行时通过 `progress.business_resume` 提供 `request_id`、`question_sha256` 两个不透明标识，平台随问题保存；回答原样保留，不从用户表单字段读取这些标识。运行时恢复相同调用，后续问题使用新的请求标识。

正常任务的 Optimize 输入来自本任务已成功的逻辑 Plan 生产者，按后置、替换、默认的顺序选择。前置、失败步骤或当前 Optimize 之后的步骤不能充当 Plan 结果。原生文件产物继续由执行器交接，业务结果 JSON 不代替这些文件。

## 独立集成测试

`POST /api/evolve/stage-skills/:implementationId/integration-tests` 接收测试 Bot、环境和 `caseInput`。测试输入可以构造，不要求引用某次历史 Diagnose 或 Plan 任务。Plan 可按构造的任务目标直接规划；Optimize 的 `caseInput.plan_result` 必须符合 Plan 结果协议。Skill Optimize 测试的原生文件与 Bench 资源由执行侧的版本化 fixture 自动准备，测试不会依赖历史任务或模型编造上游产物。

测试绑定准确实现版本，执行当前 Stage 及其前后置。为兼容原生步骤输入接口，构造的上游结果使用明确标记的 `stage-test supplied ... result` 准备记录保存；这类记录不是实际执行上游 Stage 的证据。测试结论以当前 Stage 的实际执行结果为准。

测试不绑定 Host Skill 资产；拒绝 `targetSkillAssetId` 和 `caseInput.target_skill`，也拒绝请求注入平台 `resources`、`testSkillFixture`、`stageTest`。测试不会确认、应用或登记正式 Skill 版本。

### 隔离的测试 Skill

Skill 流程中的 Diagnose 前置、Plan 替换、加固测试使用平台构造的 `stage-test-text-summary` Skill 包。新测试选择 `skill-description-v2`；历史任务保留原资源版本和校验和。资源选择依据已保存的开发记录及 Stage/位置，不分析自然语言或历史案例来猜测 Skill 身份。

`stage-test-fixture.ts` 从版本化 JSON 资源生成只含根 `SKILL.md` 的真实 ZIP，固定日期、文件权限和压缩参数。存储成功后才创建并派发测试。`config.stageTest.fixture` 保存版本、Task、ref 与 SHA256；签名 URL 仅在交付时生成。

运行时下载并校验包，在本 Task 的隔离工作区落盘；逻辑路径由运行环境映射。恢复及重试沿用同一资源和已产生的修改，不覆盖副本。路径、标记、包身份或大小校验失败必须明确报错。

Skill Optimize 测试复用同一构造包，通过已有 `skill_prepare` 准备本 Task 的候选副本。平台只在准备报告与候选路径匹配后派发 Optimize；原生执行和扩展均使用该副本，不能写回 Bot 安装源。测试完成按 StageTest 结束，不创建正式 `skill_finalize` 或版本应用。

历史测试的配置、答案和产物不迁移、不删除。新增测试不依赖旧任务中的来源快照。路由测试验证输入、隔离和编排；运行时测试验证真实文件和恢复；模型与 Bench 行为必须通过端到端执行单独验收。

### Optimize 原生输入 fixture

新建 Skill Optimize 测试冻结 `stageTest.inputFixture: optimize-v1`，CW 只分配本 Task 的训练、测试 Domain ID 和隔离 Skill 包，不定义原生文件格式。执行侧 `platform/clawevolve_runtime/stage_test.py` 根据 Task/Step 元数据识别测试，使用同目录 `fixtures/optimize-v1.json` 的固定目标、方案和四个文本摘要 Case，复用原生 Plan 文档及 Bench 模板渲染器生成输入。此准备只适用于 Skill Optimize 独立测试；正常任务及未冻结该版本的历史任务不进入。

输入记录明确标注 Mock 上游 Plan，未运行 Plan；执行侧准备成功后，通过既有报告接口更新本 Task 的测试输入记录。Optimize 替换及后置读取准备后的同一份 Plan 结果。真实 Optimize、Agent、Bench 和上报继续执行，不能由 fixture 决定其成功。

每个测试的资源 ID 独立。恢复前核对 fixture 版本、Task/owner、候选基线与原生输入校验和；已完成准备时保留文件及候选改动。发布或报告失败会中止启动，不假装准备成功。修改 fixture 正文必须新增版本，不重写旧任务输入。
