# Desktop Skill：能力覆盖与四工程包归属核对

日期：2026-09-09。状态：只读源码审计与设计范围收口；不是实现完成或验收通过记录。

用户确认Q28原验收内容，并明确要求能力市场添加SkillCenter公共Skill、云端懒物化、加入Bot能力、Desktop最终生效全链路纳入。Q29的四组结构已获认可，但用户要求用完整能力清单核对遗漏；本文件用于回答该问题，不构成编码、创建任务、部署或推送授权。

最终确认补记：用户已确认本轮覆盖分析，并于2026-09-09对“所有入口共用一套Bot级恢复任务”的进一步解释明确回复“定稿”。本矩阵与决策账本共同作为后续正式Spec/四工程包拆分的依据；这不代表实现或实机验收已经完成。

## 1. 基线与证据范围

- 本轮重新fetch Avernet `github/dev`：`ca308268927f10df887ad3543d664244dbe6ce52`。
- 本轮重新fetch OCB `origin/dev`：`658abdf1cc88b047c73463463c695510866b4759`。
- OCB实际`ocb-public` gitlink：`137219270c46a3508daeaf92d9e660e5ee38aeb9`，不等于上述公共dev。
- 工作目录HEAD没有checkout到这些提交，源码审计使用`git show`/`git grep`固定SHA；不把工作目录旧文件的行号当最新实现。
- 对比gitlink与Avernet当前dev，`src/engine`、`src/baas`及Skill Center直接核心实现无差异；Backend其它调用面存在差异，尤其Bot Config Manifest、Object Store与部分DI。不能宣称完整企业集成已一致；实现时需重验最终gitlink及装配。
- 本轮没有执行真实SC、数据库、OSS或Desktop操作，也没有运行业务测试。前端产品的真实部署代码没有核验；用户此前确认的SC详情iframe属于产品已知事实，不因仓库内demo/旧consumer缺功能而推翻。

正式实现规范见[spec.md](spec.md)，工程顺序见[plan.md](plan.md)，本地工作清单见[tasks.md](tasks.md)。[decisions.md](decisions.md)保存最终选择及关键取舍；旧研究中的候选不能覆盖Q13/Q25/Q26等最终KISS取舍，新增决定须同步正式规范。

逐HTTP方法/路径附录：[OpenAPI能力审计](openapi-capability-coverage-audit.md)。市场专线证据：[市场/Reference/Sync审计](market-coverage-audit.md)。两份附件均已完成固定源码核查，覆盖动态注册的旧接口，不以静态搜索到的decorator数量代替完整入口表。

## 2. 能力市场必须拆清两次内容准备

```text
SkillCenter市场搜索/详情（仍是外部skill_code）
  → POST skill-center-references（多个skill_codes）
  → 解析精确SC版本
  → 幂等创建/复用TeamClaw Center资产与精确Version
  → 云端Canonical Store物化成功、Version PUBLISHED
  → 最终重新校验当前目标SkillSet、权限与成员规则
  → SkillSetManagementService.add_skills
      ├─ inactive Set：只写Membership；不因这次引用下载或激活
      └─ active Set：写Membership与Installation、触发共同投影
           → Backend准备/复用精确分发包、签名下载描述
           → Engine按需下载、完整缓存、建立/切换软链
           → PENDING由Skill专用恢复任务继续推动
```

云端懒物化把外部市场Skill变成可引用的TeamClaw资产，Desktop下载把已经存在的精确版本变成设备可用内容。二者不是同一个事务、同一个任务或同一个完成点。

- **TeamClaw已有市场Skill、已发布Space工坊Skill**：按`skill_id`走已有成员添加，不要求前端再次走SC懒物化；如目标Desktop缺少Center缓存，仍复用后半段下载/投影。
- **SC Public市场Skill**：按`skill_code`走专用批量Reference；云端已就绪资产/Version复用，不能拿display name与Local/Repo合并身份。
- **Reference COMPLETED**：已完成正式添加，不等于Desktop Runtime CONVERGED。不为下载等待回退共享Version或Membership，不把Desktop下载塞回Reference任务。
- 不新增Desktop专属市场、Membership写路径、SC同步器或第二套Installation SSOT。

### 当前源码揭示的关键交接点

以下路径均相对Avernet根目录，基线为上述`ca3082689`：

1. `src/backend/src/agentclaw/community/core/skill_center/services/skill_center_reference_processor.py:240–274`：精确Version和云物化完成后，**先**调用`track_latest.version_published`，然后准备最终成员添加。首次目标Bot可能尚无该Installation，不能把这次提前扇出当最终添加的恢复保证。
2. 同文件`:360–444`：重新get_set、调用正式add_skills，按`outcome.succeeded`置COMPLETED，不以Runtime已收敛为条件。
3. `.../services/skill_set_management_service.py:523–538`：正式添加结果已经携带`runtime_projection`；Reference最终状态不保留该结果。现有Reference查询不能被文档写成“持续查询桌面下载状态”的接口。
4. `.../services/_mutation_flow.py:137–157,233–251`：inactive跳过投影；Bot未就绪、snapshot不可得时可在调用Engine前返回PENDING。G4恢复覆盖必须包括这些入口，不仅在下载器返回PENDING后建任务。
5. `.../services/bot_runtime_projector.py:125–186`及`.../services/runtime_projections/per_domain.py:83–105`：共同投影与SkillRuntimeDelivery适合集中复用，市场/SkillSet/Direct不应各自实现Desktop下载分支；任务执行仍遵守最新Reader和当前绑定。

G4应在正式变更/共同恢复入口承担PENDING后续责任，覆盖直接`apply_plan`调用和提前返回，而非只挂在某个`project()`调用点。具体窄Interface在Spec定义，不因此引入TaskQueue事务基础设施或新状态表。失败入队窗口按Q9/Q20的低频扫漏兜底，不能将Reference COMPLETED误报为持久调度已保证。

**用户进一步确认的统一性约束**：上面列出的市场、Set、Direct、启动和TrackLatest不是分别开发恢复能力。共同收尾/触发点只调用同一恢复Module，复用一个Desktop Skill task type；相同owner+bot在Queue作用域内共用一条live工作项。Handler读取最新Reader并经同一个Projector/apply观察Ready/PENDING和推动建链；不加独立下载进度probe或每入口专用Task。现有activation_sync文件只是未实现骨架，G4仍有真实开发工作。详细边界见决策账本末尾。

## 3. 逐能力覆盖矩阵

分类：**共享回归**=已有共同实现，不重复建设Desktop业务；**适配**=本期需要补齐或接线；**相邻兼容**=不开放新产品功能，但保护被共享代码影响的调用方。每一行既有主责组，又保留跨组联调责任。

| 能力/产品操作 | 本期处理 | 归属与验收重点 |
| --- | --- | --- |
| Desktop创建/首次启动/重启 | 适配+回归 | G1补Hermes创建入口；G4启动、当前绑定和恢复触发；G3准备缺失Center。 |
| TeamClaw市场搜索、列表、详情、收藏等已有入口 | 共享回归 | G1入口/字段/权限矩阵；不另建Desktop市场。SC iframe详情沿用产品现有方案。 |
| Repo仓库目录、Skill详情、README、同步 | 共享回归 | G1目录与查询，G2字节通道回归，G3/G4确认原Repo交付/投影不被Center改造破坏；不重写Repo下载器。 |
| SC市场搜索和外部详情 | 共享回归 | G1确认外部skill_code、版本/来源字段、前端调用约定；搜索不批量写ac_skill。 |
| SC市场批量Reference创建、列表和单项查询 | 共享回归+交接适配 | G1明确现有端点与异步/幂等/错误合同；G3云就绪后设备交付；G4最终add后Skill恢复。 |
| Public Reference批量部分失败、同key重放、现有自动重试 | 共享回归 | G1逐项结果，不把一个失败当全部回滚；G4已成功项仍有恢复责任；不虚构独立Reference retry路由。 |
| Public云懒物化、并发/跨Bot复用 | 共享回归 | G3接在现有Materializer产物后，不重复创建资产/Version，不重复SC下载逻辑；精确身份与Local/Repo同名隔离。 |
| 已物化Center被其它Desktop/云Bot引用 | 适配+回归 | G3复用共享Canonical和派生包，各Desktop只准备自己的缺失缓存；各Bot的Installation与运行结果独立。 |
| 可消费Space Skill列表与已发布工坊引用 | 共享回归+适配 | G1可见性、授权与已发布过滤；G3/G4复用同一Center交付，不再强制Public懒加载。 |
| Space文件夹/Git创建、草稿文件读写/Git刷新/删除、升级 | 共享回归 | G1列入API验收索引；这些是云端资产操作，不经过Desktop文件上传通道，不复制Draft Store；不凭历史Spec新增当前Router没有的整包Draft replacement接口。 |
| Grants/Editor Request/Lease/Publication及Retry | 共享回归 | G1既有权限/状态机/并发令牌合同；G4只承接发布后的Track Latest，Desktop未下载完成不改变云发布成功判据。 |
| 工坊下线、血缘/影响面、复制独立Skill | 共享回归 | G1必须列出既有入口；使用当前已合入业务语义，不从历史讨论恢复旧退役/自动Draft行为；Desktop引用遵守共同血缘模型。 |
| Bot Skill列表/详情/active与LOCAL过滤 | 适配+回归 | G1完整Center Query及当前Bot记录投影、Reader读取；不能只实现Canonical文件读取而遗漏Router前置校验。 |
| Bot Skill内容、共享README | 适配+回归 | G1明确Bot-scoped内容与botless README的访问合同；Center基于精确Published内容，非Engine缓存是否Ready；Local历史owner+default定位风险要收口。 |
| Bot-local上传、替换、删除与文件读取 | 适配+回归 | G1沿用业务/权限/文件校验；G2双向字节保真；G3原Local映射兼容，失败不伪造文件成功。 |
| SkillSet创建/编辑/删除、开关、成员列表及增删 | 共享回归+交付适配 | G1按现有普通/Default/exclusion/R1–R3规则；G3下载/映射；G4正式成员/开关变更后的恢复。active/inactive分别验收。 |
| 单项Direct activate/deactivate（即使当前UI未开放） | 适配+回归 | G1 Center Query与Bot grant前置校验；G3/G4共同命令交付恢复，不新增Desktop私有Direct策略。Local DELETE不扩成删除Center共享资产。 |
| 参数GET/PUT | 适配+回归 | G1仅已定存储/误吞错修复；G2请求响应兼容；不把Native参数消费列成已经保证。 |
| Skill依赖MCP、显式MCP/CLI及Default Policy | 共享回归 | G1保持当前API与权限行为；G3不重复Scanner推导精确Version依赖；G4只隔离Skill等待，不每5秒重推MCP/Passport，不新增MCP任务。 |
| SC周期性/手动同步，Space/Public更新后Track Latest | 共享回归+适配 | G1现有手动入口，G4共用同步完成→版本通知→候选→Reader→投影；G3按最新exact下载。无SC webhook、无Desktop第二同步器。 |
| 老版本升级、断网/重连、重试过期及漏唤醒 | 适配 | G3旧合同保护与缓存状态；G4自动恢复，零依赖用户再次变更；仅Skill完成，不掩盖MCP其它域失败。 |
| Deprecated Skill OpenAPI与Legacy BFF兼容 | 共享回归 | G1识别动态legacy_route及旧调用合同；G3共享投影变更保兼容，不让旧接口成为Desktop专属第二写路径。 |
| 真实企业DI/任务注册/配置/客户端与镜像装配 | 必须核验 | 各组自己的协议/DI/测试负责，G4集成收口；OCB gitlink与最终Engine/客户端版本明确，不能只测Avernet Fake。 |
| Bot Config Manifest/CLI工具平台、Service Artifact | 相邻兼容，非Desktop新功能 | 不开放整个配置平台或Desktop服务发布。保护共享LocalUpload/Direct/Reader/文件适配路径的现有云端行为。 |

这份矩阵确认的是“每类能力有归属”，不是“上述全部已实现或实测成功”。逐HTTP方法/路径清单与市场源码细节见同目录审计附件。

## 4. 四组职责应如何补写

- **G1：Skill API/市场/工坊兼容与缺口补齐。** 原标题“既有API补齐”过窄，须包含完整入口清单、Center访问Adapter、Bot-facing记录与权限校验、共享README、参数、Hermes创建及前端接口说明。对已有市场/Space业务以回归为主，不重复开发。Center是共享资产，Bot-facing记录只在返回投影中带目标Bot身份，不能把持久资产改成该Bot所有；Direct activate前可尚无Installation，不能以“必须已经安装”替代消费权限校验。
- **G2：Desktop文件字节通道。** 保持Q27最小范围，同时覆盖上传/替换/读取/参数文本/启动health等实际消费者，不只测试一个Rust函数。
- **G3：Center从Canonical到设备的统一交付。** 消费者同时包括SC市场Reference、已物化市场、Space工坊和Direct/Set；不只实现“给定UUID下载一个ZIP”。补齐分发、权限/描述、Engine内容Adapter、结果与跨仓字段透传。
- **G4：所有入口的自动恢复及整体联调。** 必须包括市场最终add、普通Set/Direct、启动/重连、Track Latest，以及调用Engine前的PENDING；不是只有“一个定时任务+测试”。当前mutate、listener、Task都复用正式Projector，不复制有效能力计算。

G4验收不仅逐入口可用，还要证明跨入口共用：同一Bot由市场/Set/TrackLatest同时触发，至多一个live Desktop Skill任务，每轮处理最新期望，不复制下载/退避算法，不生成三个独立恢复循环。

前端联调归属：G1交付按现有真实endpoint编写的调用/结果说明，G4协调真实产品前端验收（包含用户已确认的Desktop离线禁变更、SC详情iframe、Reference已添加与设备生效区别）。这是核验已有产品前提，不是本轮新授权前端改造。共享README没有目标Bot，不能照搬Bot content的Desired Version选择；其Center可见性与Published版本选择必须在G1正式合同中明确，不能从“content可用”推导README也已可用。SkillSet resources中的Default CLI展示也不等于具备CLI成员CRUD或独立二进制工具安装能力。

因此目前未发现为了已定Skill产品范围而必须增加第五个架构模块的理由；但若只按原四行简略标题开发，存在漏验收、漏接线的实际风险。四组必须共同引用本矩阵，不能把跨组链路划成“别人负责”而无人验收。

## 5. 尚不能宣称已证明的事项

1. 实际前端是否正确区分Reference已添加和Runtime已生效，需要后续联调；当前Reference DTO不提供持续桌面下载进度。不是本轮自动新增Runtime进度API/表的授权。
2. Bot Config Manifest明确拒绝Desktop（`.../core/bot_config_manifest/capabilities.py:325–326,363–364`），不会因为本次Skill接口适配就自动支持。它属于已定范围外的Bot配置平台能力；若用户另行要求，单独评估，不以“所有OpenAPI”悄悄扩大。
3. 未对全量产品PRD逐页面做新的前端UI验收；本轮完整性以已定Skill相关OpenAPI与关键触发链为边界。
4. 分发包、客户端/Engine升级、签名URL可达性、真实软链/hash和自动恢复效果需要实现后实机证据。当前只是基于固定源码的设计覆盖分析。
