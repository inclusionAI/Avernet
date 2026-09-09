# Desktop Skill：本地任务清单

状态：全部待实施。本文件是工程分组的工作清单，不代替后续实施Issue、PR或阶段验收记录。

Source of Truth：[spec.md](spec.md)。交付依赖：[plan.md](plan.md)。完整产品范围：[能力覆盖矩阵](capability-coverage-matrix.md)与[逐OpenAPI入口](openapi-capability-coverage-audit.md)。以下编号专用于本次Desktop改造，不与历史Phase 2分组混用。

## 通用开工条件

- [ ] B01 核对Avernet github/dev与OCB dev、实际ocb-public gitlink；记录新基线，确认已合入修复不重复做。
- [ ] B02 将本组触及的公开/内部合同、消费者、Adapter、DI、配置和测试列清；协调共享文件owner。
- [ ] B03 复核产品前提：OpenClaw/Hermes Desktop主验收、现有升级门禁和离线UI禁用；不据此删去旧端保护或新增Backend离线政策。

## G1 — Skill API/市场/工坊兼容与缺口补齐

可独立；主要Avernet，必须核验OCB装配。每项测试从正式Interface进入，不手工绕过Router前置校验。

- [ ] G1.1 实现Center Query Adapter：PUBLIC/Space可见性、owner+bot与App/协作者校验、Bot-facing只读视图；测试未安装可消费和私有拒绝。对应A02/A03。
- [ ] G1.2 接通Center详情/content和参数定义精确版本读取；云端/Desktop一致，零Engine下载，区分期望与实际版本。对应A03/A04。
- [ ] G1.3 独立覆盖共享README：Center Published选择/权限、Local owner-qualified定位、Local/Repo兼容；不猜目标Bot。对应A05。
- [ ] G1.4 深化原参数Module，明确缺失vs失败、读失败零写、单Skill替换保留其它项、写失败失败，保留地址/格式。对应A08。
- [ ] G1.5 修Hermes Desktop创建政策和正式Workflow/Router测试，不扩大其它Engine产品范围。对应A01。
- [ ] G1.6 将市场搜索/tags/收藏/Repo目录/同步、Public Reference、Space可消费和完整生命周期、Set/Direct/Default/exclusion、Deprecated/BFF分为共享回归或适配并落实测试责任。对应A09–A15/A33。
- [ ] G1.7 更新前端调用手册及结果说明：已有资产PUT、Public批量异步POST、同key重放语义、Reference完成≠Runtime收敛、内容≠设备取证；不新增进度API。
- [ ] G1.8 验证实际Protocol identity和完整DI；如公开Router/DTO改变，从Backend生成Gateway配置并核对无无关漂移。对应A34。
- [ ] G1.9 完成本组窄门禁、Standards/Spec review、高优修复及必要最终CI；分别报告外部/实机未验证项。

完成标准：Center各Bot入口实际通过共同身份链，不只是内容Store单测成功；已有云端/Local/Repo请求及参数数据不回归。

## G2 — Desktop文件通道

可与G1独立；主要OCB Desktop。外部BaaS base64与旧文本接口不变。

- [ ] G2.1 枚举正式invoke-http请求/响应所有bytes↔String转换、旧文本入口与JSON/health消费者，界定内部类型变化。
- [ ] G2.2 修内部双向bytes贯通，保留外部文本wire与合法解码；不能吞成空内容，不能改变Content-Type/状态语义。对应A07。
- [ ] G2.3 增加正式Desktop入口到测试Engine的二进制双向合同测试，覆盖UTF8/非UTF8/multipart/空body/状态/头及JSON旧调用。
- [ ] G2.4 与Local raw ZIP/文件夹上传、替换、读回联合测试，逐文件bytes/hash一致；覆盖参数文本和受影响通道。对应A06/A08/A33。
- [ ] G2.5 完成OCB规范/合同review与本组门禁，记录必须更新的客户端版本；不将代码merge当用户安装完成。

完成标准：完整通道和Local产品文件链保真，而不是仅一次echo或只修一个String转换点。

## G3 — Center统一交付

两仓配套；G3拥有分发/apply/结果及内容Adapter合同。G4消费这些合同。

- [ ] G3.1 按Spec D6/D7冻结轻量描述lookup、后台prepare、互斥包状态、字段校验、错误与真实Adapter evidence；记录调用方和所有跨仓透传点。
- [ ] G3.2 实现Canonical exact派生ZIP、独立分发namespace、可复现digest及完整发布/并发复用；不改Version、Draft/staging字段。对应A16。
- [ ] G3.3 实现可信目标内签名/过期刷新，3600秒TTL、Desktop可达endpoint及脱敏；无Center/retired-only/云端零签名/打包。对应A17/A24。
- [ ] G3.4 深化SkillRuntimeDelivery，在一处以DeviceContext.bot_type选择描述；复用同一目标上下文，公开业务不传Desktop/路径标记。
- [ ] G3.5 贯通公共DTO/Protocol、OpenClaw Adapter、OCB mapping wrapper/Hermes、Backend parser；真实解释扩展才产生evidence。对应A22。
- [ ] G3.6 实现共同内容Interface的Mounted/Downloaded选择、下载去重与有限资源占用；不重复OpenClaw/Hermes实现。对应A18/A19/A24。
- [ ] G3.7 安全临时下载/解包、完整校验/原子可见缓存、跨重启命中/轻量检查/有限清理；不做完整版本GC或Scanner。对应A18/A19。
- [ ] G3.8 接共同best-effort apply：Mixed部分结果、V2待就绪保留V1、取消不复活、显式retired/实体保护；必要最小共享修复显式报告。对应A20/A21/A31。
- [ ] G3.9 实现旧端保护、结构化脱敏错误及兼容矩阵；无证据/未知结果不换写协议、不拆批剥字段。对应A22/A23。
- [ ] G3.10 验证Mounted无下载、旧ensure只读、Strict Pool/Teclaw/云端Artifact不回归；与G1真实入口和OCB配置装配联合验证。对应A32/A34。
- [ ] G3.11 完成本组窄contract/DI/类型/架构门禁与双轴review；冻结供G4消费的最终Interface及跨仓提交引用。

完成标准：给定已授权当前逻辑计划，一次apply能正确准备/复用内容并返回逐项状态；冷包重工作可从后台独立驱动，前台不阻塞打包。不把此组完成等同自动恢复交付。

## G4 — 通用恢复与全链路收口

依赖G3正式合同；最终联合G1/G2。此组是开发任务，不是纯测试组。

- [ ] G4.1 实现一个Desktop Skill恢复Module/Handler/task type；owner+bot在原Queue作用域内去重，payload不冻结版本/URL/命令。对应A25。
- [ ] G4.2 在共同变更/投影收尾统一ensure，覆盖Reference最终add、Set/Direct、apply_plan以及未到Engine的提前PENDING；inactive SKIPPED与业务失败不误入队。对应A13/A26。
- [ ] G4.3 worker准备当前缺包后重新读取最新Reader/当前绑定，再经共同Projector做Skill-only apply；不复用长耗时准备前旧计划。对应A21/A25/A28。
- [ ] G4.4 实现5秒正常Reschedule、故障Retry、逐项完成/永久问题、30分钟期限与live-key既有限制；不创建新任务代替每轮outcome。对应A27/A28。
- [ ] G4.5 接入首次启动/重连及约10分钟分页扫漏，使用同一恢复Interface；不只扫存活Task/DB ACTIVE，不改变健康扫描保护。对应A29。
- [ ] G4.6 接Desktop TrackLatest：新增Skill等待转统一恢复；真实MCP错误仍按原行为，后续Skill轮次零MCP/Passport。对应A30。
- [ ] G4.7 测试“先fanout零候选再最终add”及市场/Set/TrackLatest并发共用单Bot live工作项，不为每入口写一套Handler。对应A25/A26。
- [ ] G4.8 测试任务终态后漏唤醒、当前绑定变化、下载中撤销/停用、权限/Offline后续变化及缓存复用；保留明确的retired限制。对应A12/A21/A28/A31。
- [ ] G4.9 验证实际DI、HandlerRegistry/启动注册、worker/扫漏配置、局部限制和全套协议；旧activation_sync骨架不被误当已实现。对应A34。
- [ ] G4.10 完成两种真实Desktop的市场→云懒物化→Set→Installation→下载→软链，以及Space发布→多Bot TrackLatest；记录逐阶段证据，非只记录202/COMPLETED。对应A10/A14/A29/A30。
- [ ] G4.11 前端联调核验已有离线禁用、升级提示、iframe详情和已添加/已生效区别；原生参数消费明确未验证，不新造进度API。
- [ ] G4.12 汇总A01–A34测试/CI/review/merge/deploy/runtime阶段状态与残余限制，完成最终整体Standards/Spec review及高优修复。

完成标准：条件恢复时无需用户第二次变更即可自动补齐当前有效Skill，所有入口共用一个恢复机制；不保证永久不可达或未知历史退出能被清理，不掩盖MCP失败。

## 最终交付记录（待实施填写）

| 组 | 代码/PR | 本地与合同测试 | CI/评审 | 合并/OCB gitlink | 部署与实机 |
| --- | --- | --- | --- | --- | --- |
| G1 | 未开始 | 未执行 | 未执行 | 未执行 | 未执行 |
| G2 | 未开始 | 未执行 | 未执行 | 未执行 | 未执行 |
| G3 | 未开始 | 未执行 | 未执行 | 未执行 | 未执行 |
| G4 | 未开始 | 未执行 | 未执行 | 未执行 | 未执行 |

本轮只有文档产出，所有复选框保持未完成。任何后续勾选都要有对应代码/测试或阶段证据，不能因为Spec定稿而勾选开发项。
