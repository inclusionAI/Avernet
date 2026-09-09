# Desktop Skill：最终决策摘要

状态：Q1–Q29总体方案及统一恢复约束已定稿；业务待实现。本文件提取最终选择和理由，原始探索中的未采纳候选不作为实施指令。详细目标合同及验收以[spec.md](spec.md)为准。

## 已定选择

| 决策 | 最终选择与理由 | 规范落点 |
| --- | --- | --- |
| Q1 范围 | Skill相关OpenAPI/市场/工坊能力适配Desktop；OpenClaw/Hermes主验收，其它已有Engine保留受影响兼容。不扩成所有Bot平台API。 | Solution、D2 |
| Q2 升级 | 新Desktop Center及bytes能力可要求客户端/Engine更新；原有能力兼容，不要求云端统一重启。 | D10 |
| Q3 与#455关系 | 复用逻辑交付seam，但不等待#455全部模块；不以本次适配改DB locator或强制切Pool。 | D5/D10 |
| Q4 内容来源 | Backend触发，Engine按需取得当前有效exact内容；不启动时全拉Center市场。 | D3/D6 |
| Q5 成功边界 | Publication、Reference添加、缓存Ready、Runtime生效分别表达；Runtime降级不回滚合法Desired State，文件上传/参数写入另守实际成功合同。 | D1/D4 |
| Q6 Hermes创建 | 修正式Local Bot Workflow的Hermes政策缺口，不据枚举存在就开放所有Engine。 | D4、A01 |
| Q7 恢复方向 | Backend通用任务重读最新Reader并投影；Engine只准备缓存，不在下载完成回调激活旧目标。 | D8 |
| Q8 部分结果 | Mixed Local/Repo/Center逐项best-effort；一个等待项不拦其它可执行项。旧DTO整批拒绝是明确兼容例外。 | D7/D10 |
| Q9 唤醒 | 启动/变更/Track Latest主动触发、任务跟进和低频扫漏进入同一恢复机制；健康扫描状态更新不是已有投影保障。 | D8 |
| Q10 频率 | 新due-now任务及时唤醒；正常内容等待5秒Reschedule；异常原退避；约10分钟扫漏。周期不是SLA。 | D8 |
| Q11 完成判据 | 看逐项可恢复工作，DEGRADED不得掩盖PENDING；停止重试不代表CONVERGED。 | D8 |
| Q12 新旧版本 | V2未Ready保留V1有效链接；切换使用新一轮最新期望，不保证旧MCP环境也保持。 | D9 |
| Q13 退出KISS | 前端离线禁SkillSet变更，Backend不新增Desktop政策。沿用显式retired，不新增退出账本、链接回执或整目录接管；接受历史retired遗失限制。 | D9 |
| Q14 派生包 | Canonical exact生成可复用ZIP，冷包重工作在后台；独立分发namespace，不复用Draft/staging字段，不新增业务Version。 | D6 |
| Q15 下载描述 | Backend签发精确对象描述，Engine直连OSS；大包不经BaaS、不直连SC，不公开私有meta或长期凭证。 | D6/D7 |
| Q16 深Module | 共用Reader/Resolver/Projector/apply；Engine通过Mounted/Downloaded Adapter准备内容；Backend新差异集中SkillRuntimeDelivery。 | D5 |
| Q17 URL期限 | 一小时签名，过期后正式投影刷新；URL不是内容身份，过期不删除Ready缓存。 | D6 |
| Q18 Ready | 首次完整校验与安全落盘后才可用；命中轻量检查。缓存元信息不是链接所有权回执，也不证明所有用户篡改都可检测。 | D6 |
| Q19 缓存清理 | 只清理确认归属且已结束的临时工作；不做完整缓存TTL/LRU/keep-N或云端派生包GC，磁盘风险可见。 | D6 |
| Q20 工作项生命周期 | 单Bot一个live工作项，30分钟单轮；重复enqueue不合并payload/加速/续期。保留既有Queue窗口，由扫漏兜底，不加outbox/generation/全局锁。 | D8 |
| Q21 MCP过渡 | Skill与MCP继续独立best-effort，保留V1链接不承诺V1业务零中断；不临时授权旧MCP依赖。 | D9 |
| Q22 apply合同 | 复用一次逻辑apply，可选下载描述与实际Adapter evidence；不新增日常probe/verify。Backend按可信bot_type、Engine按既有部署配置集中选择。 | D5/D7 |
| Q23 旧端保护 | 只在明确合同拒绝时提示升级；未知结果不换写协议。旧DTO混合请求整批拒绝不拆批补发；合法DB期望保留。 | D10 |
| Q24 Center查看 | 云端/Desktop共用Canonical精确内容读取，不看设备Ready、不触发下载；期望内容与实际运行内容区分。 | D4 |
| Q25 参数最小修复 | 保留接口、name键JSON和历史地址；读失败零写、写失败失败。不新增参数API/DB/CAS锁/自动注入；原生消费另列未验证。 | D4 |
| Q26 MCP保持现状 | 只新增Skill恢复，后续轮次零MCP/Passport重推；原MCP故障处理保留，不新增第二个MCP任务。 | D9 |
| Q27 bytes | 修Desktop内部请求/响应bytes保真，保留BaaS base64和旧文本wire；不新增上传系统。 | D4 |
| Q28 完整验收 | 包含市场SC批量Reference→云物化→正式Set/Installation→Desktop下载→实际软链，并覆盖工坊、Local/Repo及共享辅助入口。 | D3、A01–A34 |
| Q29 分组与统一恢复 | 四组分工；所有Desktop Skill入口共用一个Bot级恢复Module/task type，不能每入口各建一套。 | D2/D8、Plan |

## 统一恢复的具体边界

1. `ac_task_queue`负责持久执行和调度，G4负责一套Desktop Skill业务Handler；旧activation_sync骨架不是现成实现。
2. 所有触发源只通过共同Interface确保Bot恢复工作；按Queue作用域与owner+bot去重，不按Skill/Set/Reference/version分别排长期恢复任务。
3. Worker准备缺包后重读当前Reader与绑定，再调用共同Projector；正常apply同时观察内容就绪状态并推动映射，不再单独查百分比进度。
4. 市场物化后Track Latest可能发生在最终add之前，故正式add及共同变更收尾必须接入；Bot未就绪/snapshot失败的提前PENDING也不能漏接。
5. inactive Set的SKIPPED、权限/DB失败不是自动下载或重放业务命令的理由。Reference COMPLETED不改定义为所有设备Ready。
6. 跨入口合同测试必须证明：同Bot由市场、Set和Track Latest共同触发仍只有一个live恢复任务，读取最新目标；不能只提供三条独立入口成功测试。

## 明确保留的限制

| 限制 | 后续处理原则 |
| --- | --- |
| 遗失retired可能留下旧链接 | 不通过新扫漏推断全部链接归属；正常受支持取消场景必须通过窄测试，发现不足只评估最小共享修复。 |
| 入队与最后检查窗口、有限任务期限 | 记录真实失败并低频重新ensure；不宣称强一致或固定恢复秒数。 |
| 参数原生消费未证实 | 存储/回读与原生执行分别验收，未验证不宣传已完成。 |
| 旧MCP过渡及原MCP重试覆盖 | 保持既有行为，不掩盖失败，不扩成本期MCP治理。 |
| 完整缓存增长 | 记录容量/下载错误；不未经授权清理在用或用户内容。 |

所有源码审计均为固定提交证据。文档定稿不表示实现、CI、评审、合并、部署或实机验证已经通过。
