# 会话自愈：通用与团队定制手册

## 目标与范围

自愈同时使用通用故障知识和团队自己的业务约定。团队可以在 Skill 手册中说明某个业务场景无需干预，而不改变其他团队的处理策略，也不把整个技术故障类别排除。

本次包含：DB 维护 Bot 与团队关系、按类别合并手册引用、任务创建时冻结路由、运行时按团队准备手册文件，以及团队 Skill 手册的业务排除规则。团队是自愈独立维护的标识，不接入 OCB 空间、组织或权限模型。

不新增数据库表、环境变量、MIST 配置、AIS 快照或 Agent 输出枚举；不修改白名单、原 Bot 配置及已有任务。真实数据写入、包上传和部署不属于本地实现验收。

## 目录与职责

```text
clawevolve-recovery-handbook/
├── SKILL.md
├── common/
│   ├── mcp/handbook.md
│   └── file-download/handbook.md
└── custom/
    └── <team_key>/
        └── skill/handbook.md
```

common 保存通用处理方法。custom 按团队组织；团队的一份 Skill 手册可以按章节记录多个 Skill 的规则，不按每个 Bot 建文件。业务排除条件属于手册正文，不硬编码在任务触发器或通用路由中。

Avernet 负责配置解析、身份匹配、候选路由及冻结引用。OCB 负责专业手册正文、自愈 wrapper 的文件准备和读取引导。AIS Base 保留通用下载、安装及临时文件清理职责，不识别团队业务。

## DB 配置

复用 `ce_app_config`，key 为 `session_recovery_handbooks`，已有 `enabled` 列控制启用。通过现有应用配置管理入口维护 JSON，无需新建管理 API。

```json
{
  "bot_teams": {
    "team-a": ["prod|bot-a|owner-a", "pre|bot-b|owner-b"]
  },
  "team_routes": {
    "team-a": {
      "TC.SKILL": ["skill/handbook.md"],
      "TC.MCP": ["skill/handbook.md", "mcp/handbook.md"]
    }
  }
}
```

- `bot_teams` 按团队标识分组，每个成员固定为 `env|bot_id|owner_id` 三段，使用半角 `|`；`env` 为 `pre` 或 `prod`，各段不能为空。不同团队不能重复配置同一完整身份，同一团队也不能重复成员。
- 解析后仍用 `(env, bot_id, owner_id)` 精确匹配目标；其中 `owner_id` 与自愈目标 `entity_id` 比较，不推断组织关系。不同 Owner 的 default Bot、不同环境的同名 Bot 不共享归属。
- 一个团队可关联多个 Bot，一个目标只能关联一个团队。团队标识只允许小写字母、数字、下划线和连字符，首位为字母或数字，最长 64 字符。
- `team_routes` 的 key 为团队标识；内层 key 使用两段 TC 类别，例如 `TC.SKILL`。每个类别对应多个相对章节，不同类别可引用相同章节。
- 相对路径在 `custom/<team_key>/` 内解析；必须为由安全目录段组成的 `…/handbook.md`，不接受绝对路径、跨目录、重复章节或未知字段。映射必须有对应的团队路由定义。
- 配置缺失或显式禁用时，仅使用 common。配置存在且启用但不合法，或存储查询失败时，不创建新任务；不能静默回退 common，遗漏团队排除规则。

已存在的逐 Bot 对象数组格式继续可读，内部统一为相同的完整身份映射；新配置使用上述分组格式。团队标识是字符串，纯数字标识例如 `"97"` 对应 `custom/97/`。

配置仅管理归属和路由，不保存手册正文、不替代自愈准入白名单。增加一条团队映射不会把 Bot 加入白名单。

## 路由与冻结

1. 通过完整目标身份匹配 `team_key`。
2. 使用故障 code 的前两段，分别查找 common 目录和匹配团队的路由。
3. 将团队候选放在前面，再合并 common 候选并去重。手册目录与 TC 类别无需一一对应。
4. 将 `handbookVersion=clawevolve-recovery-handbook/v3`、`teamKey`、类别和 `chapters` 保存到现有任务创建时间线中。`teamKey` 无匹配时为 null。
5. bootstrap 使用已保存的引用；任务恢复和同一创建请求重试不查询当前团队配置重新路由。团队关系变化只影响新任务。

例如团队 A 的 `TC.SKILL.INSTRUCTION` 可以获得：

```json
{
  "handbookVersion": "clawevolve-recovery-handbook/v3",
  "teamKey": "team-a",
  "category": "TC.SKILL",
  "chapters": ["custom/team-a/skill/handbook.md", "common/file-download/handbook.md"]
}
```

候选引用是阅读引导，不表示每份正文都相关。Agent 先核对适用的团队业务约定和排除条件；未被定制的处理方法继续使用 common。明确适用的业务规则优先，但不改变授权、证据要求和用户续聊让路规则。

## 固定 OSS 包与执行文件范围

所有团队仍发布到同一个固定 Skill 包。Base 下载、解压和安装后清理下载临时目录，然后调用自愈 wrapper：

```text
固定 OSS 包 → Base 安装并清理下载 tar
           → wrapper 获取冻结的 bootstrap
           → 校验 teamKey、章节路径及文件存在性
           → 从安装源删除其他团队的 custom
           → 复制已筛选的 Skill 与手册到本执行工作区
           → 配置并启动 Agent
```

整个 common 和本团队整个 custom 可读；候选列表再指示优先阅读的章节。没有团队时移除全部 custom。安装源、自愈 Skill 内的 references 副本、独立手册副本和 Claude 链接都遵循同一范围。不能只改变 bootstrap 路径或只少复制几个文件，同时把完整目录留在安装源。

团队目录或候选正文缺失、章节跨团队、路径穿越、目录含符号链接时，启动前失败；不以省略定制规则的方式继续。筛选只操作本执行已安装包，不能修改共享源码仓库。旧执行结束后新 execution 由 Base 重新安装包；同一 execution 可按相同冻结引用重复准备。

这是固定包运行路径中的手册文件范围控制，不声明为对拥有任意宿主权限、外部源码副本或外部网络读取能力的通用安全沙箱。

## 团队 Skill 排除规则

规则在团队 Skill 手册内明确目标 Skill、具体知识或 SOP 缺口、Bot 已遵守的正常业务行为和不干预动作。Agent 核对现场后才应用；“知识缺失”标签本身不能作为排除依据。已有能力使用错误、权限拒绝或脚本失败仍需按实际故障调查。

复用现有 `blocked` 回报结束，说明业务约定、原任务真实状态及证据；不发送恢复消息、不生成额外能力建设方案、不伪造 recovered。按当前合同，这会以 failed 结束业务自愈任务，AIS 执行可正常成功，effect 保留真实判断。此变更不新增“忽略”状态，也不修改输出 Schema。

## 兼容与交付顺序

- v1 单个 chapter 和 v2 chapters 继续从保存的时间线读取，不改写历史。新包为旧的 `mcp/handbook.md`、`file-download/handbook.md` 引用准备 common 内容的本地兼容副本，旧任务不推断团队。
- 先上传包含新目录和筛选逻辑的自愈 Skill 包，再部署返回 v3 引用的后端，最后写入并启用团队配置。新包支持旧后端的 common 引用；旧包不支持新 v3 路径与团队筛选，不能反向部署。
- 分组配置需要支持该格式的后端；团队标识与包内目录保持一致。团队更名时同步更新配置引用和包内目录，既有任务的冻结引用不自动改名。
- AIS Base 不需修改。回退时先停用新配置并回退后端，再处理包版本；已有 v3 任务仍依赖对应团队手册，不能直接替换成不支持 v3 的包。
- 同一份已上传路径当前仍由现有发布方式更新；本变更冻结的是路由引用，未增加不可变正文版本或新的包版本管理系统。

## 验收

- 精确匹配环境、Bot 和 Owner；其他目标只能拿到自己的团队或 common。
- 多对多路由、自定义目录引用及缺少类别映射的行为符合定义。
- 非法配置阻止创建；SQLite 持久化及 bootstrap 能恢复冻结的团队和候选，同请求重试不受后续配置变化影响。
- 真实文件验证 common 与本团队 custom 可读，其他团队在所有安装副本中不存在；无团队、历史引用、重复准备、错误路径和缺文件均覆盖。
- runner 验证 bootstrap 和文件筛选先于 Agent 启动；真实打包测试验证发布物包含 common 与团队手册，并能导入运行。
- 原有自愈流程、用户续聊、授权和输出协议的相关回归通过。模型是否正确采纳业务排除规则需部署后的受控案例另行验收，不能用单元测试声称已经验证。
