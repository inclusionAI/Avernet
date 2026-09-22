# Pool-native 创建：已确认决策与评审记录

## 当前状态（2026-09-18）

Q14 发布顺序与适用边界已获用户确认。产品目标、范围和核心取舍评审收口；
下文保留逐轮讨论历史，旧候选不构成新的实现要求，以后续明确确认的结论为准。
正式 Spec 已整理：[OpenClaw Pool-native 创建与 Pool 稳态启动](2026-09-18-openclaw-pool-native-startup-spec.md)。
后续实现以正式 Spec 为准，本文保留决策历史。尚未开始实现、部署或扩大 Rollout。

最终范围：
1. 命中准入的新 OpenClaw Bot 创建即选择 Pool，直接初始化，不认领迁移任务。
2. 存量 Legacy 重启仍按迁移流程完成 Cutover；未重启容器不强制改变。
3. 已完成 Pool 的稳态启动/读取解除对历史 preparation/generation/ready 的依赖，
   未完成迁移及 finalizing 仍保持原恢复约束。
4. 复用布局状态表与启动回报；不新增来源列、独立确认接口、可靠投递模块或轮询任务。
5. 根级布局确认与挂载/单 Skill 健康分开；不新增逐 Skill 或挂载成功硬门禁。
6. 兼容 Backend 读取和回报字段先上线；新镜像就绪后再启用 Native 写入。
   Published Artifact 冻结合同、Desktop 本地更新路径分别处理，不由云端镜像推断。

正式 Spec 前需完成代码映射（不是重新询问已确认的产品取舍）：现有启动实例身份
如何关联 callback、BaaS 主动 alive 快路径与布局确认如何交接、Desktop 对应启动
配置/完成回报入口，以及已启用 owner Rollout 下如何安排 Native 生产方生效次序。
这些尚未以完整接口合同验证，不将设计收口等同可直接无条件上线。

## 已确认目标

- 命中 Rollout 的新 OpenClaw Bot 从首次创建起使用 Pool，覆盖 personal、desktop、Service Draft。
- 新 Bot 不进入 migration claim/cutover；存量 Legacy 可编辑运行态重启保留迁移链路。
- Pool 初始化就绪与布局选择区分；初始化失败按正常启动失败处理，不降级到 Legacy。
- 新建时明确持久化布局选择；历史缺少 layout row 的 Bot 仍解释为 Legacy。
- 存量 Service Published Artifact 的冻结布局不被当前 Rollout 覆盖。

## 简化方向（用户已确认，交接细节仍在评审）

优先复用 ac_bot_skill_layout_state，不新增 layout_origin/provisioning_mode 列。
在新 Bot 创建事务内新增该 Bot 的 layout row：active_layout=pool、target_layout=NULL、
phase=pool_initializing、migration_generation=NULL，并保存既有 rollout_evidence。
pool_initializing 为拟新增枚举值，不是新增列。启动成功后通过现有生命周期交接确认
phase=pool_active；初始化重试由原创建/启动流程承担，不新增 Skills 认领、迁移或轮询任务。

运行时路由依据当前 layout/phase 即可；“出生来源”不是常规启动分支所需事实。
不得将 migration_generation=NULL 单独解释为 native 身份，缺 generation 的历史非法
迁移记录不能因此被自动修复或跳过。需要核对所有迁移专属消费者与历史完整性校验。

复用现有 AGENTCLAW_SKILLS_LAYOUT=pool|legacy 传输语义，扩展至普通创建/重启。
Backend 从 DB/冻结 Artifact 生成部署参数；Engine 与启动脚本解析逻辑 layout。
Desktop 使用其真实启动配置交接，不能假设所有 Desktop 都经过 BaaS 云容器 env。

更激进的备选是创建时直接 pool_active、仅由 Bot PENDING/ACTIVE 表示启动就绪。
它会改变现有 pool_active 作为布局收敛证据的语义，不作为当前推荐，需额外核对
Artifact Build、运维报告、恢复、quarantine 等消费者才能决定。

## 代码核对

- types.py 的 legacy_default() 将缺行解释为 Legacy；保持其历史兼容语义。
- service_skills_manifest_env() 已输出 AGENTCLAW_SKILLS_LAYOUT，无需另造启动字段。
- reconcile_task.py 当前对 active_layout=pool 强制要求 migration_generation；
  native 流量必须从生命周期调度边界分流并处理旧排队任务，不可伪造 migration generation。
- 本文为方案记录，字段/枚举和 consumer 改造尚未实现。

## 启动成功交接：现状与建议

代码现状：BaaS create.publish_poll 观察发布 ACTIVE 后入队 create.init；
run_create_init_once() 发起容器初始化，再调用 report_device_alive() 更新 binding/Bot。
随后 BaasPublishCompletedEvent 唤醒既有迁移任务。初始化包含后台命令派发，
现有 BaaS 发布 ACTIVE 或命令成功并不能直接证明 Pool 初始化已经完成。

建议：Pool-native 的布局完成确认放在正常启动流程内、对外确认 Bot ACTIVE 之前。
Engine 启动按持久化选择准备 Pool；由当前启动实例给出实际 Pool Ready 证据。
Backend 校验 Bot/当前 binding/本次 publish 或 sandbox 身份后，幂等 CAS 将
pool_initializing 改为 pool_active，再继续正常 binding/Bot ACTIVE 交接。
Pool-native 不向 skills_pool.reconcile 入队；已有队列消息也不能要求其伪造迁移 generation。

优先复用既有启动就绪回报承载 layout 确认，但必须调研对应脚本及 payload 后决定是否
需要扩展回报字段，不能假定旧 alive 事件已携带 Pool 证据。BaaS init 的主动 alive 快路径
必须同样满足就绪条件。普通应用 /readyz、adapter /readiness 和 BaaS ACTIVE 是不同信号。
初始化成功仅表示布局已建立，逐项 Skill BEST_EFFORT 投影退化不能被无意升级为启动失败。

布局提交后、Bot ACTIVE 写入前崩溃时，正常启动任务/回报重试应补齐后续交接；
同一实例重复确认幂等，旧实例回报拒绝推进新实例。尚未承诺这几项 DB 写入已有统一事务。

## 第二轮结论（2026-09-18，Q6–Q8 用户确认）

- Q6：Pool 布局完成与单 Skill 投影结果解耦。建议以 Pool 权威目录与布局标记
  初始化完成为布局确认条件，保留既有 BEST_EFFORT Skill 投影和共享挂载失败策略。
  不因本次新增 Pool-native 把 Repo/Center mount 的 non-critical 语义升级为硬门禁。
  先前“任一 mount/Mapping 失败均导致启动失败”的示意不作为定稿合同。
- Q7：pool_initializing 期间沿用 Bot 启动中的产品限制；读取与已有控制面编辑
  按原合同处理。不因本次引入新的接口限制；现有操作原本需要就绪时仍遵循原规则。
  不新增编辑暂存、自动排队发布等机制；任何允许提前写入的既有入口必须指向 Pool。
- Q8：创建时无法可靠读取/解析 Rollout 配置，与明确未命中区分。建议无法决定
  则创建失败且不启动实例，正常未命中仍创建 Legacy；已持久化选择的重试无需重读配置。

启动就绪回报的准确 producer、传输字段及 BaaS 快路径正在跨仓只读核验；
下游接口设计待证据返回后继续评审，不把现有 alive 等同于已验证 Pool。

## Q6 检查清单（用户确认，结合第三轮挂载诊断边界）

Pool-native 初始化仅确认：
1. Engine 实际选择 pool，与 Backend 持久化布局/引擎一致。
2. Engine 自己负责的 active root、Pool Local 根初始化成功，可用于正常操作；
   只做根级检查和初始化必要操作，不递归读内容或另建全面权限检查。
3. 本次新建流程不产生平台 Legacy corpus 根或兼容 bridge；仅核对固定保留入口，
   不扫描全部 active Skill，也不自动删除异常占用的内容。
4. 当前布局初始化完成标记成功持久化，Native 合同不伪造 migration/preparation身份。
Backend 仅校验当前 Bot/绑定/启动身份、实际布局确认及预期 phase 的幂等 CAS，
不接收物理路径去重做 Engine 检查。

明确排除：Repo/Center 挂载成功或可读硬门禁、任何单 Skill 的完整性/软链收敛、
SKILL.md/Scanner/Hash、MCP权限或同步、Default skills 全成功、跨目录递归校验、
Legacy镜像拷贝/Quarantine/migration generation。普通 Engine 启动健康状态沿用
既有生命周期职责，不扩成 Pool 专属检查项目。

现有 inspect_runtime_layout 的严格迁移 probe 会检查挂载、受管active links和迁移
marker身份，不能整套复用于 Native 晋升检查；同样需审计后续reader，避免首次轻检查
但下一次readiness/Artifact读取又要求Native拥有迁移证据。

## 启动回报核验补充

daas origin/dev 65b8bfe4 的 alive仅device_id；status仅device_id/status/message。
start_service.sh的adapter /health失败只是告警继续；watchdog的SUCCEEDED一次发送即退出。
BaaS initializer后台派发后主动alive，均不能单独证明Pool布局初始化完成。
因此明确布局确认和确认投递可靠性仍需设计；不能称已有回报已满足Native合同。
是否在ACTIVE之前等待确认仍是拟议改动，必须遵守Q7不扩大产品操作限制。

## 第三轮：Legacy 创建点与挂载检查（用户已确认）

实际现状：OCB entrypoint 调用 mount_ossfs 后无条件进入迁移准备 prepare_skills_pool。
prepare_skills_pool 在缺少 active marker 时调用 _ensure_structure_bridges 和
_publish_local_copy，后者明确 mkdir Legacy Local；只传 layout=pool 不能跳过它。
select_skills_repo_mount 已消费 AGENTCLAW_SKILLS_LAYOUT，但 pool 分支还要求
layout contract version 匹配，否则选Legacy。这是已有Published合同，不是完整Native初始化。
daas setup_engine_dirs.py 消费 engine.json sandbox_setup；OpenClaw的配置仍有Legacy
Repo symlink target，需确保后续初始化不会重新创建旧入口。Published service transition
已有独立Pool制品安装清理，不能冒充新建Bot初始化。

建议：在最早容器创建配置中传递布局（及现有合同版本），启动入口在任何Legacy准备
之前分流；Pool-native分支直接准备canonical根，跳过Legacy bridge/local copy。
后续setup dirs和默认映射同样消费布局选择，不再制造Legacy入口。现有已迁移Pool及
Published artifact恢复按既有数据事实处理，不能仅凭pool参数将有历史内容的目录当新建。

挂载建议：检查与阻断分开。云上由现有mount脚本检查Repo/Center预期挂载点的实际
挂载状态（而非只用目录存在），可检查本机mount信息和预期只读/来源配置；不递归列举
仓库、不扫描Skill、不做内容hash。诊断结果与layout状态分开，可出现pool_active且
repo/center挂载异常，沿用non-critical启动策略。用户已确认检查但不新增启动硬门禁。
Desktop Repo采用下载交付时核对实际delivery类型，不能要求存在OSS mount。

## 第四轮结论：最小启动确认（Q9 用户确认）

复用现有status callback，在Pool-native启动成功回报中携带实际Pool初始化完成证据；
Backend校验当前启动身份后幂等CAS更新phase。旧alive、无布局证据的旧SUCCEEDED、
BaaS后台命令派发成功均不能替代布局确认。布局确认不能依赖激活后才执行的Skill投影。

本期沿用原回报发送机制，不新增待发送文件、ACK重试、投递任务或独立轮询模块。
容器Pool已经初始化成功但回报网络/DB失败时，接受DB暂留pool_initializing；
依赖布局终态的Service Build可能被阻断。通过用户正常重启重走初始化确认恢复，
要求初始化幂等保留已有Pool内容，不能仅因已有完成marker就跳过本次启动回报。
重复成功回报幂等。可靠投递属于后续统一生命周期治理；当前故障概率没有统计证据。
此结论取代之前“ACK前持久重试”为本期必需项的建议。

## 第五轮结论：已完成布局的重启（Q10 用户确认）

Q10建议已经pool_active的Bot重启时保留布局终态，由既有Bot/设备生命周期表示启动中
或失败。仅首次pool_initializing需要完成确认；失败后重启仍按Pool恢复，不退回Legacy。
既有Published Service重启/扩容/回滚继续采用各自冻结Artifact的布局，不取当前Rollout。

## 第六轮历史候选：Native 专用标记（已被 Q11 统一稳态合同取代）

用户要求先解释现有链路及复杂度，Q11暂停定稿；initialization=native未批准。
随后用户明确扩展本次范围：已完成pool_active的稳态运行也应解除对历史迁移证据的依赖。
下述Native专用标识方案保留为历史候选，统一稳态合同优先，尚不决定新增该字段。

现有active_marker_valid要求preparation_id匹配和字符串migration_generation，
不能直接读取无迁移记录的Native标记。建议仍使用同一路径.pool-active，增量增加
初始化来源标识（字段名待实现合同细化，例如initialization=native）；此标识仅在
Engine持久布局标记内，不新增DB列、不替代Backend布局选择。
Native标记由受控初始化写入，要求实际Pool结构初始化完成；仅对显式Native标记
免除迁移字段要求。旧标记缺少该标识继续原迁移合同，不能把缺generation的旧坏标记
自动解释为Native。后续重启、probe及Service Artifact相关消费者一致识别此标记。
不伪造preparation或migration generation，不新增一套迁移流程。以上建议待用户确认。

现有链路核对：OCB准备脚本成功生成.pool-ready/preparation_id；Backend首次claim生成
migration_generation，后续probe记录preparation_id并传入Engine activate；Engine完成
文件退休、映射和布局收敛期间写.pool-active finalizing→active，Backend最后提交
DB pool_active与Local locators。Engine marker与DB无法一次跨系统事务提交，迁移身份
用于恢复与关联。active标记的mappings是空数组，历史映射恢复材料仅在finalizing持有。
当前Engine active_marker_valid要求migration_generation为字符串，并校验与ready marker
相同的preparation_id；并非每次都向DB查generation。OCB重启准备也依赖ready+active双
marker，probe还合并挂载/active links健康检查，Artifact间接消费probe证据。
复杂性来自稳态布局事实与迁移恢复证据的耦合，Native仅加写入分支不足以解决。

## 范围扩展：已完成 Pool 的稳态合同（用户明确纳入本次）

本次包括Pool-native创建，以及已迁移pool_active Bot的正常启动/布局读取不再依赖
.pool-ready、preparation_id或migration_generation作为稳态有效性前置条件。
仍在迁移、finalizing、DB尚未完成提交的记录保持原恢复合同，不放宽迁移门禁。

建议的新统一分支（具体合同待下一轮确认）：
- Engine有效active marker表达当前Pool布局，旧active marker多出的迁移字段仍可保留，
  不要求存量回填、不主动重写或删掉历史证据；缺失或损坏的active marker不得直接当正常。
- finalizing及其他迁移阶段继续验证原preparation/generation；不因容器已经有active
  marker就跳过尚未提交的Backend迁移事务。
- Native和已迁移稳态Bot共用读取/启动规则，因此不必仅为校验分支新增native出生标识。
- 数据库phase与迁移历史分开：现有generation/quarantine继续供未完成迁移和历史清理使用，
  不因稳态读取不依赖它们就删除这些记录。
- 需要审计OCB启动准备、Engine marker/probe、Backend reconcile/运行投影以及Service
  Artifact消费者；对旧Artifact保持兼容，已发布版本的冻结layout/exact refs不漂移。

验收须增加：旧active marker在不再可用的ready/preparation历史证据下仍能正常稳态
运行；finalizing/DB未完成迁移不能误走稳态跳过恢复；Native和迁移完成者采用同一
最小布局合同；挂载/单Skill诊断不因此新增启动硬门禁。

## 剩余评审计划（预计三轮）

1. 统一稳态marker合同，并在此基础上决定缺失/损坏的恢复边界。
2. 启动回报最小字段、当前实例关联与旧镜像/旧回报兼容，核对跨仓责任。
3. 汇总新建/重启/存量迁移/Service Artifact/Desktop验收矩阵及发布顺序。
轮数是估算；若出现事实冲突先处理，不为限定轮数跳过关键决策。

Q11修订已确认：.pool-active的稳态有效性只要求engine、layout_contract_version、
activation_state=active及已定稿根级结构合同。读取时不要求migration_generation、
preparation_id、mappings或.pool-ready；历史多余字段保留并兼容，Native不写迁移字段。
不新增initialization=native分支。activation_state=finalizing仍严格走迁移恢复。
Backend phase尚在迁移中时仍需完成原事务，Engine稳态marker不替代该事实。
本项已确认；marker丢失/损坏处理及旧consumer发布次序下一步继续细化。

## Q12 已确认：启动时标记缺失与损坏

建议仅在可信启动声明选择pool时执行以下规则，不由普通probe或读取接口修复文件：
- 首次pool_initializing且marker不存在：按正常Native初始化写入。
- 已完成Pool的重启，marker缺失但Pool根级结构满足已确认合同：幂等补写最小marker；
  不读取迁移历史、不搬迁文件、不补造Skill内容、不新增全量内容检查。
- marker存在但JSON损坏、engine/contract不匹配：报告明确初始化错误并保留现场，
  不覆盖未知/矛盾事实，不回退Legacy；finalizing单独属于迁移恢复，不能视为损坏。
- 根级结构不满足合同不能只补marker声称完成；缺失状态不能依据无layout row猜Pool。
上述补写是启动幂等处理，无新增修复任务；用户已确认。

## Q13 已确认：旧启动回报兼容

建议增量扩展既有status callback，增加可选布局完成证据，不新增URL，也不要求旧
容器立即升级。原有无证据alive/SUCCEEDED继续原生命周期处理，但不据此将Native
pool_initializing晋升为pool_active。明确池布局成功证据且当前实例身份匹配时才CAS晋升。
已经pool_active的Bot保持终态，普通回报无需重复证明历史迁移；存量Legacy回报继续
原迁移唤醒流程，不能通过Native确认旁路完成Cutover。
Native启动缺少证据时不伪造成功、不切回Legacy，保留初始化未确认的诊断；沿用Q9
已接受的恢复边界。具体payload字段及复用何种现有启动身份待接口级核验，不新建身份系统。

## Q14 已确认：发布与回退边界

Q14：新版本读取旧数据/旧回报必须兼容；旧版本读取新增pool_initializing或精简
marker不天然兼容。现有Backend对phase执行枚举解析，现有Engine/OCB marker读取
仍要求迁移字段。因此先部署兼容读取和可选回报字段支持，再使Native创建与精简marker
写入生效；不能用回退旧Backend/旧镜像作为Native运行态恢复方案。
建议故障止损关闭新增准入/Native创建生产方，保留兼容读取、已选Pool事实及已有容器内容，
通过兼容版本修复。关闭Rollout不撤销已持久化Pool选择。具体如何分阶段启用生产方
需在实现计划中明确，不能假设已存在的owner rollout会自动等待组件升级完成。
本项已获用户确认，未授权本轮变更任何部署或Rollout配置。

### Q14 补充：新建／重启加载新 Engine 的适用边界

用户确认预期发布顺序为先更新 Engine 镜像，再由新建／产品重启进入 Pool。
代码核对支持这个云端默认镜像路径，不需要为未重启的旧容器额外设计 Native 初始化：
- 新建且命中准入：新 Engine 直接初始化 Pool，无迁移任务。
- 存量 Legacy 产品重启：加载新 Engine，但保留旧文件，仍须原迁移流程；
  新 Engine 不能因此删除 Legacy 迁移能力。
- 已有 Pool 产品重启：新 Engine 按 Pool 稳态启动，不依赖历史准备证据。
- 未重启容器继续原态，不因开启 Rollout 被假定已更新镜像或已完成迁移。

前提是实际模板／默认镜像已指向新版本，且没有固定旧镜像的覆盖。
BotService 的 ARCA 产品重启走 stop/start，BaaS 重启走 upgrade 并透传模板 image；
两者均不能把普通进程重启等同镜像升级。Published Service 操作继续遵守目标发布
记录的镜像／Artifact 合同；Desktop 使用本地 Engine 更新路径，不由云端镜像保证。
上述例外不扩大本次改造范围。保留先部署 Backend 兼容读取和 callback schema、
再启用新写入的发布约束；不因为 Engine 是新版而取消旧 Backend 读取新 phase 的风险。
本轮为代码路径核对，未重新核验线上模板／镜像，也未变更 Rollout 或触发重启。
