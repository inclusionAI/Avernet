# Diagnose 精确 Session 来源契约

## 创建和冻结

普通 `POST /api/evolve/tasks`（兼容 `/diagnoses`）新增可选 `sessionIds: string[]`。
既有 `sessionSource` 仍是 `local` / `service_export` 字符串，不改为对象。
创建成功后集合冻结在 `config.sessionSource.session_ids`，保留原
`config.sessionSource.mode`。未传字段不新增集合；显式 `[]` 保持未限定来源的旧行为。

Stage 独立测试通过
`caseInput.session_source: { mode: "local", session_ids: [...] }` 提供集合。
官方 Diagnose catalog 声明此可选数组；路由在 Case schema 验证后、fixture
存储和 Task 创建前执行精确校验，将同一规范化集合保存到 `caseInput` 与
`config.sessionSource`。无 Diagnose 的普通任务或其他 Stage 测试不接受非空集合。

每项只能是精确 `sessionId` 或完整 `sessionKey`，支持中文；按原值匹配，不 trim、
不改大小写、不做子串匹配，也不从诊断目标的自然语言中提取 ID。
字符范围对齐 Diagnose Python guard：Unicode 字母/数字、下划线、点、冒号、连字符；
长度 1–512 个 Unicode 字符；禁止以点开头、`..`、`.json` / `.jsonl` 后缀、
路径分隔符、空白及 shell 表达式。去重保留首次出现顺序，非数组、null、非法项返回 400。

非空集合当前仅支持 `local`。显式 `service_export`，或普通任务实际运行目标要求
自动选用服务态导出时，返回 400，不能悄悄忽略集合或改成全量读取。
这些验证失败不会创建 Task、写 fixture/目标 Skill 快照或派发 Step。

## 命令及重试

默认 Diagnose 从冻结 config 生成重复 `--session-id`；每个 ID 使用
`quoteCommandArgument` 转义，前导连字符值使用 `--session-id='…'`，避免被 argparse
解释为新选项。模板渲染、前置成功回调后的默认 Diagnose、失败重试均使用同一 helper。

重试按冻结 config 处理，不读取请求中新的 `sessionIds`。有非空冻结集合时，
只替换旧命令顶层的 Session ID 参数（包括 argparse 可识别的前缀缩写），
不修改引号内的诊断目标文字。没有模板的旧命令分支也不能丢掉范围或叠加旧 ID。
有非法冻结集合/不能安全处理的命令时返回 409，不创建重试 Step；
不修改旧 Step 命令、历史选择或历史报告。未限定来源的旧任务不被重写或补造集合。

## 运行时边界与验收

控制面只校验和冻结标识，不读取 Bot 容器目录。Diagnose runtime 将 ID/完整 key
映射到当前 Bot 的 session 索引，校验全部映射的唯一性、存在性和同 Bot 路径边界后，
才读取任何会话正文或 trajectory；未知、歧义、路径越界或软链明确失败，不能回退全量。
runtime 拒绝与 `debug-session-path` 冲突或非空集合搭配 `service_export`。

精确集合是读取上限，原有时间窗口、数量上限与业务筛选仍生效；不保证每条来源成为 Case。
不引入 Plan、fixture、Stage 调度、接受/应用或历史回填行为。

`session-scope.test.ts` 验证 schema、ID 语法、Unicode、去重与命令参数边界；
真实隔离 route 测试验证创建冻结、前置回调、重复回调、模板和原命令重试、
无参数兼容，以及非法/不支持组合在副作用前拒绝。
这些是内存数据库和模拟外部依赖上的协议回归，不代表已部署或真实 Judge 验收成功。
