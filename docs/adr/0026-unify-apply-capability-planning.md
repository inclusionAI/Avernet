---
status: accepted
---

# 将相互依赖的能力纳入同一个 Apply Category

2026-11-04 确认、2026-11-05 归档：Direct Skills、普通 SkillSet/Default 与 Direct MCP 共享一个规划和校验范围，由一个 Materialiser 执行 resolve/plan/write。分开执行时，dry-run 无法通过真实写入把 Set 变化提供给后续 Skills/MCP；采用跨 Materialiser 预测状态会扩大调度耦合，因此选择聚合，保留 Orchestrator 逐 Category 三阶段，不做并行化改造。

代价是能力内任一编译或解析失败将阻止整组能力写入；聚合不提供跨资产准备、Bot 关系与 Runtime 的整体事务。Genome 上层不扩充，本期共用层兼容旧声明，集合与 Default 保持不参与。输入包装与报告兼容已由 Q34/Q36 确认，resolve 接入由 Q41 确认；失败继续沿用 Q19 的现有异常协议，不新增部分失败回执。具体合同及验收见[原 Spec](../specs/2026-11-01-manifest-skillsets/Spec.md)。

2026-11-05 补充确认 Q35-R：取消 Orchestrator 外层 `_set_conflicts` 与失去消费者的 `ChecksSetSupply`，将现有占用及目标冲突统一放入能力 resolve。占用失败不再拒绝其他 Category；这是已接受的失败范围变化。Q34/Q36 同时确认新旧输入严格互斥、原文绑定保持、所有新执行统一聚合报告及历史解码兼容。

2026-11-05 补充确认 Q39/Q40：普通集声明与删除计划按 name 收敛，不绑定计划时实例 ID；沿用现有事务、行锁和 Apply 互斥，接受普通管理 API 的并发同名创建或占用修改，不增加跨入口并发保护。单次批量关系事务保留，不能据此承诺跨请求串行化。

2026-11-05 补充确认 Q41–Q43：保持 resolve(ctx, entries)，聚合声明最终采用下述方案 B，复用 Declaration 子类及报告视图；Center 首次准备在现有 Skill 锁事务中补复用/拒绝业务守卫，不扩大平台并发治理；能力发现采用单一执行类别、既有传输表及四种引用支持列表，不引入通用多维矩阵。

2026-11-05 Q41 方案 B 修订：CapabilityDeclaration 继承现有 Declaration，DesiredState.capabilities 存放声明。declaration() 保持 Declaration[Any] | None 和字段映射读取；declared/only 沿用通用枚举与裁剪。子类负责单聚合 entry、子报告身份和汇总编译失败；通用 Declaration、Materialiser、Compiled、Context、计划/结果及异常 API 不增加字段或参数。旧输入构造时归一一次，裁剪后不得被旧字段重新恢复。独立声明类型及联合返回类型方案撤回。
