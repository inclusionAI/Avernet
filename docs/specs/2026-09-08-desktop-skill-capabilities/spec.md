# Desktop Skill 能力适配 Spec

版本：1.0，2026-09-09。状态：总体设计已定稿；本文为Q1–Q29最终决定的规范化整理，功能尚未实现、测试或部署。

Owning Module：Skill Center。参与方：Avernet Backend/Engine/BaaS契约与OCB企业装配、Desktop客户端及Engine Adapter。目标分支：两仓`dev`。

## Problem Statement

用户希望在Desktop Bot上使用与云端一致的Skill产品能力：从TeamClaw或SkillCenter市场添加Skill、引用能力工坊已发布Skill、管理SkillSet、上传Local文件，以及在发布新版本后自动更新运行时。

当前云端Center通过挂载内容库取得文件，Desktop没有同等OSS挂载。仅将逻辑映射推过去不能生成缺失的精确版本内容；首次启动或添加后即使返回PENDING，也不能依赖用户再次修改SkillSet来补齐。市场Reference的COMPLETED只表示添加成功，不能作为Desktop已生效的证据。

同时存在共享Center Query占位、参数加载失败被视为空配置、Desktop二进制转发不保真和Hermes创建入口限制等缺口。必须接通完整入口及企业装配，而不是只实现一个下载器。

## Solution

保留一套Skill领域逻辑、Installation Reader和运行时投影。在现有逻辑apply下，为Desktop补齐按需精确版本下载：Backend准备/复用Canonical派生包及限时下载描述，Engine下载、校验、缓存并使用自己的布局解析建链。

所有Desktop Skill入口共用一套Bot级后台恢复机制。它复用`ac_task_queue`，每轮读取最新期望并调用共同Projector；不为市场、SkillSet、Direct、启动和Track Latest各写一套恢复。已有云端挂载、Strict Pool迁移和Teclaw Whole Artifact行为不变。

验收主目标是OpenClaw和Hermes Desktop。其它既有Engine保留受影响的合同兼容，不据此宣称所有Engine已完成Desktop产品上线。新增能力允许要求升级Desktop客户端及Engine；不要求现有云端容器统一升级或重启。

## User Stories

1. 作为Desktop用户，我希望使用产品已支持的OpenClaw或Hermes创建Bot，以便Skill入口与实际Engine能力一致。
2. 作为用户，我希望查看当前Bot的可达Skill及active状态，以便区分期望启用与设备实际同步结果。
3. 作为用户，我希望用LOCAL筛选当前Bot上传的Skill，以便不混入我的其它Bot资产。
4. 作为用户，我希望搜索TeamClaw市场并引用已有Skill，以便继续使用既有Repo供给。
5. 作为用户，我希望搜索SC市场、使用标签并查看现有详情，以便选择公共Skill而不先导入整个市场。
6. 作为用户，我希望一次选择多个SC Skill加入SkillSet，以便用原有异步批量接口完成引用。
7. 作为用户，我希望重发同一请求保持幂等，以便不重复创建共享资产或成员。
8. 作为用户，我希望批量引用的一部分失败时保留成功项，以便逐项理解结果。
9. 作为用户，我希望云端懒物化成功后正式检查最新权限与SkillSet状态，以便后台等待期间的变更仍受约束。
10. 作为用户，我希望同一已物化版本被其它Bot引用时复用共享资产，以便避免重复导入。
11. 作为用户，我希望向inactive SkillSet添加成员不会提前激活，以便组织内容与实际启用保持区分。
12. 作为用户，我希望开启SkillSet后自动下载缺失的Center内容并建链，以便无需第二次操作。
13. 作为用户，我希望引用能力工坊已发布Skill时沿用同一设备交付，以便不再经过Public市场懒加载。
14. 作为工坊协作者，我希望原有Space、Draft、Grant、Lease、发布及重试接口保持一致，以便不学习Desktop专用资产流程。
15. 作为工坊用户，我希望下线、影响面和复制沿用当前产品规则，以便Desktop引用也遵守共享资产治理。
16. 作为用户，我希望查看有权访问的Center详情、内容和README，以便不因本地下载尚未完成而无法查看共享资产。
17. 作为API调用者，我希望Direct操作复用已有权限与成员规则，以便Desktop不出现另一套激活语义。
18. 作为用户，我希望首次启动和重启自动补齐所需版本，以便不手工刷新SkillSet。
19. 作为用户，我希望已有精确缓存可跨重启复用，以便不反复下载相同文件。
20. 作为用户，我希望单个Skill下载等待不阻断其它可执行项，以便混合Local/Repo/Center仍可部分生效。
21. 作为用户，我希望V2未就绪时保留V1有效链接，以便不提前指向半成品目录。
22. 作为用户，我希望下载期间取消激活后不会被迟到下载重新激活，以便最新期望始终有效。
23. 作为用户，我希望断网恢复后系统继续同步，以便不依赖再次点击产品按钮。
24. 作为用户，我希望签名URL过期后系统取得新凭证，以便不需重新发布或重新导入Skill。
25. 作为用户，我希望升级Skill后所有适用Desktop自动跟随最新已发布版本，以便使用新内容。
26. 作为用户，我希望看到PENDING、DEGRADED及具体问题，以便不把“已添加”误认为“已生效”。
27. 作为用户，我希望Local文件夹中的图片、ZIP等内容完整上传、替换和读回，以便文件不被文本编码损坏。
28. 作为用户，我希望参数保存保留其它Skill配置，以便一次读取异常不会清空整个配置文件。
29. 作为用户，我希望参数读取/写入失败如实返回，以便不收到虚假的空配置或保存成功。
30. 作为已有云端Bot用户，我希望此次Desktop改造不改变挂载、布局、历史制品和原有兼容路径。
31. 作为平台维护者，我希望多个入口对同一Bot共用一个恢复工作项，以便控制重试成本并集中排障。
32. 作为平台维护者，我希望Backend只表达逻辑映射，由Engine决定物理目录，以便Desktop与Legacy/Pool保持正交。
33. 作为平台维护者，我希望验证实际OCB装配、客户端和Engine，而不只看到公共仓库Fake测试通过。
34. 作为用户，我希望市场收藏只是收藏，以便不会因此下载、安装或激活内容。

## Implementation Decisions

以下条目是目标合同，不表示同名现有类已经实现；Implementation可以在保持合同的前提下使用现有小Module，不要求逐条新增Service。

### D1. 事实、身份与成功层级

- Bot地址始终是`owner_id + bot_id`，保留现有tenant/env作用域。actor不是owner，`default`不全局唯一。
- `ac_bot_skill_installation`表示Bot当前active身份；Effective Read继续经过统一Reader及现行flush模式，不重新union Set/Default/exclusion。
- Center运行时身份是内部`skill_uuid + sc_version_number`，逻辑入口使用`link_name`。外部SC code与名称都不能代替内部UUID；Public导入复用来源身份，不与同名Local/Repo合并。
- 实际消费版本来自正式VersionResolver；不同Bot的Desired State、缓存和结果各自独立。MCP dependencies来自精确Version，不从下载文件重新Scanner扫描。
- Publication成功以既有Canonical就绪及Version发布合同为准，不等待Desktop传播。
- Reference COMPLETED表示正式添加完成，不代表Runtime CONVERGED。缓存Ready也不代表软链已经生效；列表active不代表设备观察结果。
- 合法Set/Direct Desired State提交后不因Runtime PENDING/DEGRADED回滚。权限、DB和领域校验失败仍是真失败。Local文件上传和参数保存必须实际写入成功，不能套用Desired State的成功语义。

### D2. 四个工程包及共享职责

| 工程包 | 职责 | 不得另建的重复能力 |
| --- | --- | --- |
| G1 Skill API/市场/工坊兼容 | Center Query及相关入口、README、参数错误保护、Hermes创建、共享业务/旧接口回归与调用文档 | Desktop专属市场、Space、ACL或Installation实现 |
| G2 Desktop文件通道 | 双向bytes保真、旧文本入口兼容、Local完整文件流程 | 新上传会话/分片/OSS中转系统 |
| G3 Center统一交付 | 精确派生包/签名、共同apply扩展、MOUNT/DOWNLOAD Adapter、缓存及单轮映射 | 每Engine下载器、每入口物化服务、Backend路径表 |
| G4 通用恢复与整体接线 | 一个Bot级Skill工作项、全部触发源、扫漏、Track Latest协调与集成验收 | 每入口Task、MCP恢复Task、Pool迁移替代者 |

所有组自带测试。G3只证明一次交付/内容准备，G4提供无需用户再次操作的自动跟进；G3单独合并不等于功能完整上线。

### D3. 市场与工坊的完整消费链

既有TeamClaw市场资产和已发布Space Skill按内部`skill_id`加入Set。SC Public按外部`skill_codes`走专用异步Reference：解析exact版本、复用/创建资产及Version、云端物化、最终重新校验并调用正式SkillSetManagement。

- 保留现有多code、去重、幂等冲突、逐项状态和部分失败合同；不新增独立Reference retry接口。同key重放不是重置终态FAILED，新key代表新命令。
- active Set添加维护Membership/Installation并投影；inactive添加只维护成员，不因Reference完成提前下载。之后activate按届时期望投影。
- 最终add失败保留可复用的共享资产；不得回滚其它成功Reference项或把失败伪装为Desktop等待。
- 物化后的Track Latest可发生在最终add前，不作为首次目标Bot恢复保证；最终共同变更入口必须接入D8的恢复机制。
- 周期/手动SC Sync继续只处理已物化Public资产，不全量导入市场，不新增webhook或Desktop同步器。新版本与Space发布同走既有通知/扇出，再交给共同投影。
- 市场列表、标签、详情iframe、收藏、Repo目录/详情/同步及工坊辅助接口必须在验收索引中出现；已有共享逻辑不重写。

### D4. Query、内容、README、参数和Local文件

**Center Query**：补齐现有占位Adapter，使详情/content/parameters/Direct共用的前置解析、目标Bot记录及权限检查均成立。共享资产持久归属不变，只构建Bot-facing返回视图。Direct activate前可以没有Installation；不能用“已经安装”替代消费权限。

**权限**：复用Bot owner/协作者与应用Grant，以及PUBLIC/Space可见和消费规则。Bot访问权不赋予任意私有Skill读取权；不新增编辑申请、Owner/Manager或Lease门槛作为普通查看条件。

**Bot content**：同一Query与Canonical读取用于云端和Desktop，解析当前期望的精确Published Version，不要求设备在线或本地缓存Ready，不调用Engine、不准备下载包。期望V2而实际仍V1时允许展示V2，但不能据此称V2已运行。普通Asset/Draft/Version读取不新增flush；Bot active查询仍服从Reader。

**共享README**：与Bot content分别覆盖。保留Local/Repo既有读取兼容并修复Local owner+bot定位；Center没有目标Bot时按共享资产可见性及最新合格Published Version展示，不猜某个Bot的版本、不展示Draft、不触发设备下载。显式版本内容继续走既有Version接口。资产Offline的展示与消费限制沿用正式资产规则，不借README恢复可消费性。

**参数**：保留公开GET/PUT、按Skill name组织的JSON及历史地址。深化原参数Module：明确文件缺失才允许初始化；超时、拒绝、代理错误、非法JSON/结构均不能当空配置。PUT完整替换单Skill对象，保留其它合法项及元信息；读失败零写，写失败返回错误。维持既有参数名/必填校验，Center定义来自精确版本。复用原Factory/内部地址兼容及设备FS，不新增Engine参数API、DB表、分布式锁或自动注入。原生参数消费仍独立未验证，本期只保证存储与回读。

**Local bytes**：沿用现有raw ZIP/文件夹上传、同名替换、删除与文件读取；Desktop内部HTTP body双向使用无损bytes，外层BaaS base64合同不变。旧文本/JSON命令在边界保留原wire，不把body改成JSON数字数组。状态、Content-Type、空文件及必要HTTP头保持正确；不吞解码/读取失败。不更改既有安全校验和Local文件迁移协调规则。

### D5. Backend逻辑交付与Engine内容准备

共同调用方向保持：正式业务命令 → Desired-State UoW → Reader/VersionResolver → 纯RuntimeProjectionResolver → BotRuntimeProjector → SkillRuntimeDelivery → Engine。

| Seam | 责任与约束 |
| --- | --- |
| 既有SkillRuntimeDelivery | 接收逻辑计划/retired；集中解析可信目标、准备下载描述、调用apply并解释结果。调用者不传is_desktop、物理目录或下载策略。 |
| Backend精确分发能力 | 依赖Canonical及对象存储Protocol，提供派生包就绪/失败结果和限时描述；不维护Installation或自行投影设备。 |
| Engine内容准备Interface | Mounted与Downloaded两个真实Adapter；只回答精确内容可用性并在DOWNLOAD模式准备缓存。 |
| Engine共同映射应用 | 根据内容Ready和当前合法逻辑计划做best-effort建链/退出；不由下载完成回调重放激活。 |
| Desktop恢复Module | 统一ensure工作及执行轮次；依赖现有Queue/Reader/Projector，不复制它们的业务。 |

Backend新分流集中在交付Module，依据owner-qualified DeviceContext的`bot_type == desktop`，不是provider、engine名或layout。分类、签名作用域和实际invoke复用同一次可信目标解析；身份/绑定异常不靠猜测签名。历史合法空bot_type保持现有兼容。

Engine Composition Root复用已有agentbox配置事实选择内容Adapter，不让请求强制MOUNT改DOWNLOAD，不在业务层散落环境变量判断。OpenClaw/Hermes共享下载实现。Legacy/Pool与交付方式是独立维度；本期不切流、不迁移DB locator、不建立整库active桥。

Mounted保持原挂载及检查行为，不下载、不修mount、不写只读仓库。Teclaw仍由现有Registry选择Whole Artifact，不被当成普通文件型MOUNT。

### D6. Canonical派生包和设备缓存

1. 首次被Desktop需要时才从Canonical exact内容生成派生ZIP；不从Draft、Publication staging或SC latest获取替代内容，不改变Version状态。
2. ZIP身份包含内部UUID、精确版本、Canonical内容摘要及分发格式版本，跨Bot可复用。使用独立分发命名空间，不放入被云端挂载的Runtime内容树，不复用主Skill的Draft/staging字段。
3. 冷包的重工作只在后台恢复执行中准备。前台投影只读取既有包描述或返回该项PENDING并确保后续工作；不能在用户请求中同步读取整版本、压缩上传后再返回。
4. 构建结果只有完整验证并发布后才可签名。并发构建要保证精确身份结果一致、未完成对象不可读；采用可复现归档和既有对象存储一致性原语，不新增DB事实表或分布式锁框架。
5. 只为当前desired Center签发精确对象GET URL，有效期3600秒；retired-only和云端请求不准备包/签名。地址来自部署配置，必须Desktop可达，不复制Repo的公开meta供私有内容使用。
6. URL不写长期任务payload、普通日志或前端参数；不传长期OSS/SC凭证。更换URL不改变内容身份，不取消同identity/hash的有效下载；过期时后续投影更新凭证，不使Ready缓存过期。
7. Engine直连OSS下载，包字节不经BaaS/Desktop HTTP隧道。只允许本合同可信来源描述，保留受控网络来源、超时、大小及并发限制；不能把此接口变成任意地址下载器。
8. 下载/解包使用本Module独占临时空间；验证摘要、大小、精确身份、安全路径/链接条目、展开数量/体积和规范入口，完整后才发布为缓存Ready。不新增Scanner或MCP重推导。
9. 首次/重新下载完整验证，缓存命中轻量检查；目录存在不等于Ready。内容就绪元信息只证明缓存完整，不记录软链所有权。轻量检查不承诺识别全部用户篡改。
10. 同设备相同exact内容合并下载；限流未启动、正在下载、临时失败与已Ready应可区分。进程重启可由后续apply重新准备，下载器不是永久激活队列。
11. 本期不做完整缓存TTL/LRU/keep-N及云端派生包自动GC。只安全清理自己已结束或可确认不再运行的临时工作，不清理未知/运行中目录，不以删除用户或在用文件腾空间。磁盘增长和不足保持可诊断。

### D7. 共享apply内部合同

继续使用`POST /api/skills/mappings/apply`。原`mappings`、`retired_mappings`、`source_layout`及Mapping v2/v3身份不变；本期不创建Mapping v4、产品下载Router或日常probe/ensure/verify组合。旧`center/ensure`仍只读，Strict迁移仍使用原合同。

下载型请求可附带顶层`center_content`。以下字段是本Spec对已定语义的wire收敛；不是已部署DTO：

| 字段 | 合同 |
| --- | --- |
| `contract_version` | 当前为1；与Mapping身份版本独立。 |
| `packages` | 当前desired Center精确身份的内容描述集合，同exact去重；不包含仅退休项。 |
| 每项`skill_uuid`、`sc_version_number` | 精确逻辑身份；必须能关联当前desired映射，不接受任意OSS key。 |
| 每项`state` | `READY`、`PENDING`或`UNAVAILABLE`，只表示云端分发包描述，不表示设备就绪。 |
| READY项 | 必填`package_sha256`、`package_size`、`signed_url`、`expires_at`；完整类型验证后才可下载。 |
| PENDING项 | 包尚未准备完；不伪造空URL/hash，不阻断其它可执行映射；由恢复工作后台准备。 |
| UNAVAILABLE项 | 携带稳定问题code和可恢复性；不得透出内部异常/URL，也不能将权限拒绝当正常下载中。 |

状态采用互斥类型，READY必填字段不得被普遍扩大为optional以隐藏错误；不属于本状态的字段不能被消费。具体长度/数量/路径限制复用当前精确版本与包安全合同，并在对应DTO/配置及测试中统一，不另造不一致规则。

响应沿用逐mapping的identity、action、status、code、retryable与用户问题明细。Downloaded Adapter在现有`evidence.center_content`中报告真正解释的合同版本和实际mode；不得由Router机械echo。该证据不代替缓存或软链成功。

- 缺失/重复/不属于请求的结果、矛盾aggregate、缺有效合同识别证据均不得伪报CONVERGED。
- Backend要求DOWNLOAD而实际配置MOUNT时明确报告配置/能力问题，不静默下载或改挂载；能安全执行的其它项仍按新端逐项规则。
- 正常冷内容PENDING、下载失败、缓存Ready但建链失败必须保留区别；不增加单独进度查询RPC，apply同时观察内容状态并推动当前映射。
- 所有Protocol、公共Engine Adapter、OCB wrapper/Hermes、请求和响应parser必须贯通扩展，不能手工重建payload丢字段。
- 422等错误可能回显签名URL；传输Adapter需结构化、脱敏传播，不解析异常字符串猜合同版本，不日志记录原始validation输入。

### D8. 一套Bot级恢复，而非每入口一个任务

复用`ac_task_queue`底座，新增/补齐一个Desktop Skill task type和Handler。旧activation_sync骨架不是现成能力，也不因相似名称直接复制其历史动作payload。

**调度身份**：Queue既有`env/app/task_type`作用域加`owner_id + bot_id`；同Bot至多一条live工作项，不按入口、Skill、Reference或版本拆长期任务。payload只承载逻辑目标及必要控制信息，不存签名URL、冻结版本清单或待重放的激活动作。

**入口**：正式Set/Direct变更收尾（含Reference最终add）、共同投影、启动/重连及Track Latest都请求同一恢复Interface。Bot未就绪或snapshot失败导致未进入Engine的提前PENDING，也要进入这一共同机制。inactive SKIPPED不是下载理由，业务权限/DB失败不是重试历史命令的授权。

**一次执行**：核对目标仍适用及当前绑定；Reader读取当前有效身份并解析精确版本；后台逐项准备尚缺派生包，单项准备失败不能阻断其它可执行项；重工作结束后重新读取最新期望，再调用共同Projector的Skill scope及Engine apply；按逐项结果决定本行Queue outcome。已退出的目标不继续安排下载，迟到内容不能重新激活；任务自身不再enqueue另一条任务来代替Reschedule。

| 情况 | 后续 |
| --- | --- |
| 新建due-now工作 | 使用现有wake_on_enqueue及worker领取，首轮不等待扫漏。 |
| 内容正常准备/下载中 | `Reschedule(5s)`，不因正常等待指数退避。 |
| 网络/设备暂不可用 | 使用既有`Retry`退避；超时不能当已确认在下载。 |
| 混合DEGRADED/PENDING | 看逐项可恢复工作，永久问题不能遮住其它等待项。 |
| Skill域全部完成 | 结束快速跟进；不表示MCP/Passport全部完成。 |
| 只剩不可自动解决的问题 | 停止该问题高频重试，保留具体DEGRADED，不能伪报成功。 |
| 单轮达到30分钟deadline | 按现有Queue终态/释放live key机制处理；不回滚DB、不删除完整缓存。 |
| 漏唤醒、过期或较长离线 | 约10分钟分页扫漏，幂等ensure同一类任务；不在扫描线程下载/投影。 |

兜底不能只查现存任务或DB ACTIVE/状态跳变；必要时对目标范围内存活且有绑定的Desktop幂等再核对。允许已有完成Bot低频核对，不新增同步进度表。现有健康扫描只更新状态，不能偷换其dry-run/白名单保护或视为已经触发投影。Pool过渡期继续尊重既有迁移对映射的所有权，不让新任务抢写。

5秒是下一次可领取时间；10分钟是扫描周期，均不是端到端SLA。deadline不是取消正在运行Handler的硬超时，租约续期不延长它。重复enqueue不更新payload、不提前run_at、不延deadline，亦不是所有前台命令的分布式锁。

保留现有入队非事务窗口，以及最后一次读取至Complete之间出现新期望的窗口，由已定扫漏兜底。不得因此新增outbox、generation表、任务进度表或修改通用Queue基础设施。入队失败必须保留真实错误/日志，不能声称持久恢复已保证。

### D9. 版本切换、退出和MCP

- V2未Ready时保留V1有效链接；Ready后由基于最新Reader的正式apply切换。Engine下载完成回调只发布缓存，不重放旧激活。
- 当前显式retired和best-effort保护保持共用，不新增持久退休账本、Engine链接回执或全命名空间清理；不能把指向某种内容根等同平台所有权。
- 前端已确认Desktop离线禁用SkillSet开关/成员变更；Backend不新增专属offline拒绝，Direct API保持。前端禁用不是技术强一致保证；直接API、操作中断网仍可能丢失retired并残留链接，扫漏不能凭空恢复这些事实。
- 正常在线“V1保留而V2下载中停用”与“首次下载中取消激活”必须验证。若原共享退出规则不足，提出最小一致性修复，不暗中升级为整库删除或回执系统。
- Skill/MCP独立best-effort保持现状：V2等待时可能已应用新依赖，V1链接保留不保证旧MCP环境或业务零中断。
- Desktop恢复只做Skill scope，后续轮次零MCP/Passport重推。Track Latest仅因Skill下载等待时转交共同恢复，不被这个新等待驱动原全域Retry；真正MCP失败的既有处理保留。不新增MCP专用任务。

### D10. 兼容、升级与公开接口

保持既有产品OpenAPI方法、请求身份、幂等和分页；不新增Desktop市场/下载/进度/参数接口。当前已有资产加Set是PUT，Public Reference是异步POST，二者不能混用；Local raw ZIP与Space文件夹合同分别保持。源码不存在的独立Reference retry或整包Draft replacement不可从旧Spec恢复。

| 组合/情况 | 必须保持的行为 |
| --- | --- |
| 新Backend + 旧/新云端Engine | 不附下载扩展、不打包、不要求重启；MOUNT/原fallback不变。 |
| 旧Backend + 新Engine | 既有无扩展云端/Local/Repo合同兼容；不承诺旧Backend能启动新Desktop Center下载。 |
| 新Backend + 新Desktop | 一次apply，逐项best-effort，统一后台恢复。 |
| 旧Desktop明确拒绝新增字段/能力 | 准确升级提示，停止该问题高频重试；DB期望保留。旧DTO整批拒绝时不能声称Local/Repo当次已执行。 |
| 网络超时、5xx、代理404、非法响应/缺证据 | 表达结果未知/协议问题，不当升级证据，不切另一写协议。 |

仅已识别的结构化unsupported/新增字段拒绝可以作升级依据，不是任意422。旧端整批拒绝不剥字段、不拆批补发。标准路由404+同目标health的原有窄识别可以保留，但需要新下载合同的请求绝不fallback到旧mount/publish路径；本次不增加正常稳态probe。

Desktop既有产品升级门禁继续使用，不新增Backend最低版本表。门禁是用户确认的产品前提，不证明所有旧请求绝不可能到达；保护分支和组合测试必须保留。

公开Router/DTO如实际变更，必须从Backend正式实现生成Gateway产物并执行合同检查；不能只修改生成JSON或只更新前端说明。签名扩展为内部Engine合同，不直接成为前端字段。

## Testing Decisions

以用户可观察行为、正式Service/Plugin Interface及真实Router→DI为测试面。Mock应模拟外部依赖而非跳过需要验证的Module；必须证明真实Adapter被调用。共享Mounted/Downloaded运行同一契约套件，具体实现可补充测试。

测试分层：各组窄单测与协议/DI/架构门禁 → Standards/Spec双轴review并修高优问题 → 必要全量/CI收尾 → 匹配OCB gitlink与真实Desktop验证。已有CI要求不削弱；Fake/隔离测试不能代替真实设备。此文没有运行这些测试。

### 验收索引

| ID | 必须验证 | 主责 |
| --- | --- | --- |
| A01 | OpenClaw/Hermes Desktop创建、启动，现有其它Engine兼容 | G1/G4 |
| A02 | Bot列表/LOCAL筛选/active/分页，owner+default隔离 | G1 |
| A03 | Center详情/content/参数/Direct均通过真实Router前置身份与Grant | G1 |
| A04 | 云端/Desktop同权限content一致且零Engine/下载；期望V2不代表实际V2 | G1 |
| A05 | README无Bot语义、Center Published可见性、Local owner定位 | G1 |
| A06 | Local文件夹/raw ZIP上传替换读取，文本/非UTF8/图片/空文件hash一致 | G2 |
| A07 | 字节修复不破坏JSON/health、状态与Content-Type；失败不伪空 | G2 |
| A08 | 参数读失败零写，写失败失败，替换A保留B及历史格式/地址 | G1/G2 |
| A09 | 市场列表/tags/详情/收藏与Repo目录/详情/同步，零全市场物化 | G1 |
| A10 | Public多codeReference从接收到云物化、最终add、Desktop软链完整链 | G1/G3/G4 |
| A11 | 幂等重放/冲突、批量部分失败、已物化跨Bot复用；无伪retry接口 | G1/G4 |
| A12 | 物化后权限/Offline/Set状态改变时最终检查；失败保留共享资产 | G1/G4 |
| A13 | inactive引用不下载；后续activate下载；重复成员changed=false后仍可恢复 | G3/G4 |
| A14 | Space已发布引用复用同一Center交付；Draft/Grant/Lease/发布重试等共享回归 | G1/G4 |
| A15 | 下线影响面/复制按当前合同；不创建旧Spec中的自动Draft行为 | G1 |
| A16 | 冷派生包后台构建、并发可复用、完整发布、无重复压缩/Scanner | G3 |
| A17 | 签名1小时、过期刷新不重启有效下载/失效缓存、私有内容不公开meta | G3 |
| A18 | hash/大小/路径/链接/数量校验，半成品不可见，安全临时清理 | G3 |
| A19 | 缓存跨重启复用、轻量检查及磁盘不足可见；无完整版本自动GC | G3 |
| A20 | mixed Local/Repo/Center逐项结果完整；一项PENDING不拦其它合法项 | G3 |
| A21 | V2准备中保留V1；新目标停用/移除后迟到下载不复活 | G3/G4 |
| A22 | apply扩展逐层透传、真实Adapter识别证据、缺项/重复/矛盾结果不伪成功 | G3 |
| A23 | 旧云端/新云端/旧Backend/新Desktop组合，旧端拒绝不拆批补发 | G3 |
| A24 | 分类依据bot_type且集中；同provider不同部署、Teclaw不误入文件apply | G3 |
| A25 | 市场/Set/Direct/TrackLatest/启动同Bot共用一个live任务，读取最新期望 | G4 |
| A26 | 先fanout零候选后final add，以及未到Engine的PENDING仍进入共同恢复 | G4 |
| A27 | 下载中5秒、异常退避、混合DEGRADED/PENDING、完成判据及永久错误 | G4 |
| A28 | 30分钟期限、重复ensure、终态释放/扫漏、最终检查竞态与当前绑定 | G4 |
| A29 | 首次/重启/重连及漏唤醒自动补齐，不依赖用户第二次变更 | G4 |
| A30 | 手动/周期SC Sync、Space发布→TrackLatest→Desktop新exact；恢复零MCP/Passport | G4 |
| A31 | 不新增离线Backend政策/R1/R2/R3，正常显式退出与实体保护正确 | G3/G4 |
| A32 | Strict Pool过渡所有权、Legacy/Pool正交、云端mount和历史Artifact不回归 | G3/G4 |
| A33 | Legacy BFF/动态Deprecated OpenAPI、Default/MCP/CLI共享调用面回归 | 各组 |
| A34 | OCB真实Protocol identity/DI、Task注册、签名网络、客户端/Engine版本与真实文件证据 | 各组/G4收口 |

真机验收每条关键消费链至少记录：调用/trace及Reference/Attempt ID（适用时）、目标owner+bot、期望exact、任务状态、Engine版本与实际链接目标/可读内容hash。不得记录签名URL/长期凭证。分别报告本地测试、CI、评审、合并、部署及实机结果。

## Out of Scope

- 新增Desktop支持的Engine产品、Aix Engine重写、全量Pool切流、云端mount/启动脚本重构、#455剩余全部模块。
- 整个Bot Config Manifest/CLI工具平台开放给Desktop、Desktop Service Artifact；只保护受影响云端共享调用面。
- 新Desktop资产/Installation/参数/进度/退休表，通用Queue事务outbox/generation/锁基础设施，按入口恢复Task。
- 独立Engine拉取Desired State接口、下载完成自动激活回调、SC webhook、整市场下载、重复SC Scanner。
- 完整缓存自动GC、强制清理全部非期望链接、离线Backend专属禁令、追回已下载字节或失效URL即时终止已有流。
- 新MCP持久恢复/Skill-MCP原子切换、原生参数注入或消费能力、参数全局并发CAS/断电原子性。
- 新前端下载百分比/Runtime进度轮询API，重写已有市场详情iframe或将Reference COMPLETED改为设备全部Ready。
- 承诺永久断网/权限拒绝/受保护实体冲突一定能成功，或将调度周期宣传为严格恢复SLA。

## Further Notes

### 参与模块的现行合同入口

以下链接定义现有Module/协议。D7描述的是本次待实施的兼容扩展；阅读时先区分当前合同与目标合同，不能把本Spec的新增字段视为当前Engine已经接受。

| Module/调用方向 | 现行合同 | 本次关联 |
| --- | --- | --- |
| Backend Skill Center | [模块维护约束](../../../src/backend/src/agentclaw/community/core/skill_center/AGENTS.md)、[Context Boundary](../../../src/backend/src/agentclaw/community/core/skill_center/README.md) | 统一Reader/Writer、资产与设备结果分离、DI和公开入口。 |
| Backend调用Runtime | [Projector Service API](../../../src/backend/src/agentclaw/community/core/skill_center/bot_runtime_projector_protocol.py)、[Projection结果合同](../../../src/backend/src/agentclaw/community/core/skill_center/runtime_projection_contract.py)、[Runtime ports](../../../src/backend/src/agentclaw/community/core/skills_pool/ports.py) | G3/G4复用共同投影；兼容Strict Pool与原逻辑apply。 |
| Backend内容与存储 | [Canonical Store](../../../src/backend/src/agentclaw/community/core/skill_center/canonical_center_store.py)、[ObjectStorage Plugin API](../../../src/backend/src/agentclaw/community/plugin_api/object_storage.py) | 精确内容读取、派生包和签名依赖；不复用Draft身份。 |
| Engine Skill能力 | [SkillsService Protocol](../../../src/engine/src/engine/community/core/skills/protocol.py)、[当前HTTP DTO](../../../src/engine/src/engine/community/api/skills/schemas.py)、[异构Engine与mapping合同](../../../src/engine/docs/heterogeneous-engine-architecture.md) | 新扩展须透传到真实Adapter，旧ensure/迁移合同保留。 |
| BaaS设备/PAAS调用 | [Device管理Service API](../../../src/baas/src/secbaas/community/api/device_manage/_protocols.py)、[PAAS Service API](../../../src/baas/src/secbaas/community/api/paas/_protocols.py) | `invoke_http_in_device`与设备地址作用域；保留既有HTTP/base64转发语义。 |

OCB为企业私有仓库，不能用失效的本机相对链接冒充公共合同。配套固定ref为`384146cff15758dae2817e7069321b94bfdcee51`：Desktop现行请求/响应类型在`src/desktop/bdc-crates/bdc-mng-api/src/types.rs`，Container Interface在同crate的`container.rs`，内部bytes Plugin API在`src/desktop/bdc-crates/bdc-plugin-api/src/container.rs`；Engine透传消费者在`src/engine/src/engine/corp/engines/pool_mapping_payload.py`和`hermes/skills.py`。企业实现者按该ref及真实最终gitlink核验G2/G3，公共模块测试不替代这项检查。

### 权威与追溯

本文是本次Desktop改造的规范入口；[plan.md](plan.md)描述交付顺序，[tasks.md](tasks.md)为四组本地工作清单。[决策摘要](decisions.md)保留最终选择及关键取舍；实现以本文为规范入口，原始探索和被替代候选不是执行输入。新增用户决定仍优先，须同步本文，不能只更新聊天。

[能力覆盖矩阵](capability-coverage-matrix.md)、[OpenAPI入口审计](openapi-capability-coverage-audit.md)、[市场专项](market-coverage-audit.md)提供代码事实和完整入口附录，不把审计时的缺口写成已实现。直接模块规范和既有公开wire仍须在实现时核验。

本轮固定研究基线：Avernet `ca308268927f10df887ad3543d664244dbe6ce52`；OCB `658abdf1cc88b047c73463463c695510866b4759`；实际gitlink `137219270c46a3508daeaf92d9e660e5ee38aeb9`。两仓dev为后续目标，开工前必须重验；本文件没有宣称这组代码已部署。

沿用[稳定Skill身份](../../adr/0001-stable-skill-identity-across-versions.md)、[Track Latest与历史制品](../../adr/0002-market-and-space-skills-track-latest.md)、[版本/发布/传播状态分离](../../adr/0009-separate-draft-attempt-version-and-propagation-state.md)、[托管Skill写入权威](../../adr/0010-teamclaw-is-authority-for-managed-skills.md)。不重定义Space生命周期或编辑租约；旧全局词汇表的Effective集合计算/租约等陈旧实现描述不能覆盖当前Installation/领域合同。

### 决策追溯

| 已定讨论 | 本Spec落点 |
| --- | --- |
| Q1–Q3 范围、升级、与#455正交 | Solution、D2/D5/D10、Out of Scope |
| Q4–Q6 来源、成功边界、Hermes入口 | D1/D3/D4/D6 |
| Q7–Q10 通用后台、部分结果、唤醒及频率 | D7/D8 |
| Q11–Q13 完成判据、旧链接、退出KISS | D8/D9及A20/A21/A27/A31 |
| Q14–Q19 包、签名、深Module、缓存 | D5/D6/D7及A16–A19/A24 |
| Q20 单Bot工作项与Queue窗口 | D8及A25/A28 |
| Q21 Skill/MCP过渡 | D9及A30 |
| Q22–Q23 内部apply及旧端保护 | D5/D7/D10及A22–A24 |
| Q24–Q25 内容/参数 | D4及A03–A05/A08 |
| Q26 MCP保持现状 | D9及A30/A33 |
| Q27 Local字节保真 | D4及A06/A07 |
| Q28 全能力市场/工坊验收 | D3、User Stories、A01–A34及覆盖矩阵 |
| Q29 四组、全部入口共用恢复 | D2/D8、plan/tasks、A25/A26/A34 |

### 已接受风险与实施澄清

Q13遗失retired、Q20任务窗口、Q21旧MCP过渡、Q25原生参数消费、Q19磁盘增长是有意识保留的限制，不是遗漏待补的隐形项目。真实前端离线禁变更/升级门禁仍需联调，不能当成Backend强制或不可达分支证明。

签名/分发前缀、局部并发/网络限制及配置透传优先复用现有配置和存储原语；实现时在G3契约提交中记录有效值并覆盖测试，不额外构建配置平台。若源码事实使已批准权限、数据兼容或退出语义无法成立，先报告精确冲突，不扩大方案或带病声称交付。

本变更只固定文档基线，不包含业务实现、部署配置、数据库或生成OpenAPI产物的变更。开发与运行验收状态以任务清单的证据记录为准。
