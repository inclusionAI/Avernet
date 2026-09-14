# Desktop Skill：四工程包交付计划

状态：文档基线；实施工作尚未开始。目标：Avernet与OCB均基于`dev`；规范以[spec.md](spec.md)为准。

## 1. 交付原则与顺序

G1/G2为可独立验证的补齐，G3提供统一单轮交付，G4提供通用自动恢复和完整集成。每组自带测试，不能把全部测试推迟到G4。

| 阶段 | 内容 | 进入下一阶段的条件 |
| --- | --- | --- |
| 开工核对 | 两仓最新dev、OCB实际gitlink、已合入修复、共享文件owner | 不重做已完成变更，不以公共dev等同企业部署。 |
| G1/G2 | API兼容补齐、Desktop字节通道，可独立推进 | 各组窄合同/DI/测试及review完成；无需等待Center下载。 |
| G3 | 分发包、统一apply/内容Adapter、单轮映射 | 跨两仓字段/错误/证据贯通；Mounted和Downloaded合同通过。 |
| G4 | 单Bot恢复、所有触发源、扫漏、Track Latest隔离 | G3结果及后台准备Interface稳定，G1/G2最终版本用于集成。 |
| 企业验收 | 实际OCB gitlink、客户端/Engine、预发+真实Desktop链路 | 关键产品矩阵有独立运行证据，不用CI代替。 |

G3合同设计可在G1/G2实现期间推进；同文件改动须明确owner，不默认四组同时任意改DI/Projector。G4只依赖G3的明确Interface，不复制临时实现。

预计基础PR结构为G1 Avernet、G2 OCB、G3 Avernet+OCB、G4 Avernet；若G1/G4确需企业专属装配/配置修改，则增加对应OCB配套PR。这个估算不是PR数量硬合同；以职责完整、少stack和可验证为准。

## 2. G1 — Skill API/市场/工坊兼容与缺口补齐

### 范围

- Center Query真实Adapter：PUBLIC/Space可访问性、目标owner+bot、Bot-facing返回视图、精确Published解析；覆盖详情/content/parameters/Direct共同前置门禁。
- Center内容与共享README分别落合同；Local README使用持久owner+bot，保留Local/Repo行为，不把共享资产写成Bot所有。
- 参数加载错误保护及原Factory/FS兼容，读失败零写、写失败失败、单Skill替换保留其它项。
- Hermes Desktop新创建入口的实际政策修复；不扩大Engine产品范围。
- 按入口附录核对市场/收藏/Repo/Space完整生命周期/SkillSet/Direct/旧接口；现有共享业务以受影响回归为主，不逐路由重写。
- 更新前端调用说明：已有资产PUT与Public Reference POST不同；Reference已添加与Runtime生效不同；不虚构retry/download/进度接口。

### 主要现有seam与文件（定位提示，不是独立事实源）

- Backend `SkillQueryServiceProtocol`及`core/skill_center/services/skill_query_service.py`。
- `core/skill_center/services/skill_parameter_service.py`、`factories.py`及已有DeviceFileSystem。
- `core/bot_inventory/policies/combo_policy.py`与Local Bot Workflow。
- OpenAPI `skills`、`skill_sets`、`spaces`、`market`、`repository_catalog`及Authorization/Admission。
- 既有SkillCenter/Space/Version DI；完整OCB Corp provider链必须核对，不只手工构造Service。

### 验收与边界

负责Spec A01–A05、A08–A15和A33中共享API部分。测试通过真实Router/DI运行；覆盖User/App及私有资产拒绝、不同owner的default、未安装但可消费Center。参数原生消费另行标记未验证。

不改Runtime下载、MCP恢复、Space发布状态机、Source导入规则或Config Manifest Desktop支持。不因前端当前未开放Direct而不测其现有API。

## 3. G2 — OCB Desktop双向bytes

### 范围

沿用BaaS外层base64，修复Rust内部有损String转换；请求和响应都必须端到端bytes。旧文本命令的JSON shape保留在原Adapter边界，启动health/其它JSON消费者按bytes解析。

主要消费者：BaaS invoke-http、Desktop management API/container plugin/Engine HTTP manager及旧文本handler。对应OCB目录为`src/desktop/bdc-crates/`下的mng-api、core-runtime、core-plugins、adapter与plugin-api。Avernet的文件FS/HTTP契约作为对照，不为此另建上传系统。

### 验收与边界

负责A06/A07及A08/A33的通道回归。至少有从正式Desktop入口到测试Engine HTTP服务的双向集成，涵盖multipart图片/ZIP、非UTF8、空body、UTF8、状态/Content-Type及文本旧入口；再与真实Local上传/替换/读回逐文件hash对照。

不经此通道传Center派生包；不修改Skill业务、OSS签名或云端协议。新客户端需实际发布安装才能证明修复，公共仓库CI不是客户端部署证据。

## 4. G3 — Canonical到设备的共同交付

### 范围

- 一份精确派生分发能力，复用Canonical和对象存储；不是Draft Builder或Repo整库同步器的别名。
- 分发Module必须提供轻量“已有描述读取”与后台“精确包准备”两类操作。前台只走前者；冷包重工作由G4调用后者，再使用共同投影。授权/签名留在可信目标交付中，不向任务暴露任意OSS key。
- 固定派生归档格式/可复现输出、完整发布原语与缓存identity；尽量复用现有存储/安全能力。无新业务表、公开meta或Scanner。
- `SkillRuntimeDelivery`集中获取同一DeviceContext并按bot_type装配可选`center_content`；不在各业务Router判断Desktop。
- 公共Engine DTO/Protocol、OpenClaw Adapter、共享mapping contract、OCB wrapper/Hermes全链路传播字段及结果证据。
- Engine Composition Root选择Mounted/Downloaded，后者承担下载、去重、安全校验与缓存生命周期；共用既有映射/retired/best-effort。
- 旧端精确拒绝与未知结果分开；传输错误脱敏、无自动剥字段/换协议写入。

### 主要现有seam

Backend：CanonicalCenterVersionStore、ObjectStoragePlugin、SkillRuntimeDelivery、SkillsPoolRuntimeProtocol、DeviceContext/DeviceAdapterTransport、RuntimeProjectionResult。

Engine：SkillsService/apply请求结果、content prepare Adapter、layout resolver、映射应用。OCB：Pool mapping payload wrapper、Hermes adapter、Desktop镜像/配置注入与企业OSS实现。

具体代码入口见两份审计，避免用研究工作树的旧HEAD当当前文件。新字段schema由G3唯一负责，G4消费，不另起同义DTO。

### 必须冻结的内部合同

| 合同 | 最低内容 |
| --- | --- |
| 精确分发 | lookup与后台prepare的状态、namespace/格式/digest、完成发布、签名TTL/错误、并发复用。 |
| apply扩展 | Spec D7互斥描述状态、字段校验、结果逐项关联、真实Adapter evidence与旧端拒绝。 |
| 内容准备 | Mounted/Downloaded的Ready/Pending/Error、副作用、去重及进程生命周期。 |
| 后台调用 | G4可以准备当前缺包而不在前台做重I/O；准备后应重新读取最新目标再正式投影。 |

负责A16–A24、A31/A32、A34相关合同；与G1/G2执行可达入口集成。须证明正常在线V2下载中停用V1和首次下载中取消不复活；若原retired规则不满足，报告最小共享修复，禁止改成全目录接管。

G3单独可以合并兼容的构件，但不把冷包PENDING、人工调用prepare或单测下载成功当完整功能上线。

## 5. G4 — 通用Bot恢复与整体接线

### 范围

- 一个Desktop Skill恢复Module及一个task type，所有触发源共用；worker读当前Reader和当前绑定，不持久重放旧动作。
- 在共同变更/投影收尾接入ensure，覆盖正式Reference最终add、Set/Direct、apply_plan，以及Bot未就绪/snapshot失败的提前PENDING。不能在每个HTTP Router各加队列实现。
- 启动就绪/重连/扫漏使用相同Interface；不悄悄给健康扫描dry-run增加设备副作用。
- 后台先准备当前需要的缺包；若准备发生等待/重工作，正式apply前再次读取最新期望。最终投影仍由共同Projector解析，不能复用准备前的旧激活计划。
- 正常PENDING 5秒Reschedule、真实故障Retry、30分钟单轮期限、约10分钟分页扫漏；重复ensure、终态释放和最后检查竞态遵循原Queue限制。
- Desktop Track Latest仅将新增Skill等待交接到此任务，保留真正MCP故障的现有处理。Skill任务自己不调用MCP/Passport。
- 完整企业DI、任务启动注册、有效配置及生命周期装配；旧activation_sync骨架必须明确未被误当已接线实现。

### 最重要的三个集成测试

1. 市场先物化、fanout运行时零候选，随后active Set正式add返回PENDING：仍能ensure通用任务并自动下载建链。
2. 同一Bot同时从市场、SkillSet和Track Latest触发：只有一条live恢复任务；最新Reader包含的所有相关Skill最终可处理，不各写一个入口Task。
3. Engine下载完成前发生取消/改绑定：后续任务使用新事实；缓存完成回调不复活旧目标，任务不把旧签名和计划发送给新绑定。

负责A10–A15的消费桥接、A25–A34整体链路。前端联调需核验既有离线禁用和升级前提、Reference结果文案，不新造Runtime进度表/API。

## 6. 共享触点与并行冲突管理

| 触点 | 主责 | 其它组如何使用 |
| --- | --- | --- |
| Query/参数/Local create及公开字段 | G1 | G2/G3不复制业务校验；字段变更生成OpenAPI由G1负责。 |
| Rust HTTP body及文本边界 | G2 | G3内部apply JSON必须走兼容通道，不能另开专用socket。 |
| 分发/apply/evidence/Engine内容接口 | G3 | G4按正式类型消费；不为测试复制同义类型。 |
| 恢复任务key/outcome/触发及Track Latest | G4 | G1/G3仅通过共同窄Interface接入，不复制延迟/重试逻辑。 |
| Skill DI/配置/生成产物 | 当前改动所属组 | 报告准确符号/路径，顺序集成，不把整个DI模块覆盖回旧版本。 |

任何任务改动若涉及其它组主责seam，先同步最小合同变化；同一配置/协议只有一个定义，不能以最终resolve冲突为由并行建立两套算法。

## 7. 验证、交付和发布边界

每组依次运行贴近改动的UT/contract、Router/DI/architecture/类型门禁，再做Standards/Spec双轴review并修高优问题，最后按风险与仓库要求执行必要全量/CI。Backend全量不在每次小改后反复运行。真实Corp外部服务与真实Desktop分别验证，隔离实现不能冒充生产一致性。

合并/发布时记录Avernet SHA、OCB SHA和实际gitlink、客户端版本、Engine镜像及有效配置。兼容构件可先集成，但完整能力验收要求G1–G4配套结果；发布/切流须另行授权，不新增灰度控制平台，不因本计划自动重启现存Bot。

回退不删除DB期望态、共享资产、完整缓存或历史locator。旧Desktop或旧Backend不一定具备新Center能力，回退可以失去新能力但不得伪报成功；保留既有升级提示及云端兼容。禁止以全量清理磁盘或回滚业务表作为默认修复手段。

## 8. 本轮产物与下一步

本基线产物：正式Spec、四组计划、任务清单及覆盖/源码附录。文档PR不代表实施Issue已创建或功能已完成。

下一步按组建立自包含实施Issue并记录依赖，再开始对应实现。每组任务在领取时重新核对最新两仓基线和本Spec，不以旧阶段编号G1/G2/G4或过去Phase 2同名分组误认这次Desktop工作已完成。
