"""CentralizedExecutionAdapter 内部编排核(事件驱动 + 状态条件触发)。对齐 plan.md §3.0。

非独立模块,TaskService 内部实现细节,对外不暴露。构造期收传输端口(bot/bcs/discover,由 DI 从配置注入),
``_build_*`` 内部 new 引擎自带策略(TaskPlanner/TaskDispatcher/TaskRunner)+ 接线 TaskExecutor(三模态投递+poller)。
引擎自身实现 ResultSink(poller 终态回投直接调 on_report)与 TaskContextBuilder(执行上下文派生),
消除"先建 stub 再外部注入真实 body/接线点"的后填,无引擎子类化、无 reach-in setter。验收 100% 走 on_report
回投(gap 计算即验收,无主动 verify dispatch);BBS 与其它模态统一经 runner.start_run 投递。
零 case 知识:engine 不含任何节点名字面量。测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。

Step2 改造(状态机解耦 + PlanResult + 显式 target + harness 执行报错区分):
- 状态机:PLANNING=规划中(显式委托态;on_pass 翻父 RUNNING→PLANNING 后 plan),RUNNING=执行中(子执行/自身执行);
  add_task_nodes 翻父→RUNNING(委托)。终验=根 gap 闭(plan 返 []+has_gap=F)→翻根 DONE + 图 DONE。
- plan(graph, target_node_id):on_execute→None(自发现根)/on_pass→parent/on_fail→failed 叶/on_miss→miss 叶。
  返 PlanResult(children, has_gap, gap_detail) 四象限:children→add+dispatch;空+has_gap=F→gap 闭 DONE;
  空+has_gap=T→深度闸门(升 BBS/HUNG)。
- harness 执行报错(exec_error):bot 压根没跑通(run FAILED/SLA/poll 耗尽)≠ 验收不过(run COMPLETED+FAIL)。
  执行报错→on_harness 复位 RUNNING→PENDING 重投;计 harness_retries,达 MAX_HARNESS(默认 2)→HUNG 不再流转。
  验收不过(acceptance FAIL+gaps)→on_fail 补救重规划(深度闸门)。

协程化(CR 反馈:任务执行是耗时任务):全链路 ``async def``。锁内 await plan/dispatch(同 task 串行 IO,设计意图);
投递/拉群 IO 锁外 await,gather+Semaphore 下沉。副作用收集:on_* 锁内 async collect → 锁外 ``_drain`` await 执行。
注:``threading.RLock`` 跨线程正确串行;corp 单持久 loop 并发同 task 回投需切 ``asyncio.Lock``(ocb 接入时定)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    NodeAction,
    NodeOpResult,
    PlanResult,
    Status,
    TaskCallbackData,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    effective_run_mode,
    TaskNodeQueryCriteria,
    task_spec_instruction,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_context.task_trajectory.models import ReasonCatalog
from agentclaw.community.core.task.task_runner.callback_adapter import (
    EXEC_ERROR_ORIGIN_BOT_INTERFACE,
    EXEC_ERROR_ORIGIN_PARSE,
    EXEC_ERROR_ORIGIN_TERMINAL_INVALID,
    EXEC_ERROR_ORIGIN_TRANSPORT,
)

if TYPE_CHECKING:
    # Protocol + trajectory-enum imports are TYPE_CHECKING-only: the engine stores
    # the optional task_context_service + passes enum members through the service's
    # ``emit_trajectory_event``, and ``from __future__ import annotations``
    # stringises these hints so they are never evaluated at runtime — keeps the
    # engine's runtime import surface minimal. ``ReasonCatalog`` is imported for
    # real (used by the EXECUTE/VERIFY gate's ``_EXEC_ERROR_ORIGIN_TO_REASON``
    # origin→error_type mapping, REQ-5).
    from agentclaw.community.core.task.task_context.task_context_service import (
        TaskContextServiceProtocol,
    )
    from agentclaw.community.core.task.task_context.task_trajectory.models import (
        TrajectoryActionType,
    )


logger = logging.getLogger("task.centralized_lifecycle")

_DEFAULT_MAX_HARNESS = 2  # 执行报错 harness 重投上限(达上限→HUNG)

# REQ-5 — exec_error_origin(由 ``callback_adapter`` 透出到 patch 的
# ``extend_props_patch["_exec_error_origin"]``)→ ReasonCatalog 映射,engine EXECUTE/VERIFY
# 闸门据此设置轨迹事件的 ``error_type``。origin 字符串以 ``callback_adapter`` 的常量为
# 单一真相源(避免漂移);未映射的 origin 值→ ``error_type=None``(gate 防御性降级)。
#
# ``transport`` origin 在本 EXECUTE/VERIFY 闸门是 **dormant-but-retained**:``plan_call_fail`` /
# ``dispatch_exception`` 发生在 PLAN/DISPATCH 闸门(分别由 P3-2 PLAN 轨迹捕为 ``plan_failure``、
# P3-1 DISPATCH 轨迹捕 dispatch 类结局),不途经 EXECUTE/VERIFY;无 prod 路径在此 surface
# transport origin。该映射 + ``_classify_exec_error_origin`` 的 transport 分类单测覆盖之,留作
# 末来若 patch 携带 transport origin 时的 gate 能正确分类(不新增 fire 路径)。
_EXEC_ERROR_ORIGIN_TO_REASON: dict[str, ReasonCatalog] = {
    EXEC_ERROR_ORIGIN_BOT_INTERFACE: ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
    EXEC_ERROR_ORIGIN_PARSE: ReasonCatalog.PARSE_ERROR,
    EXEC_ERROR_ORIGIN_TERMINAL_INVALID: ReasonCatalog.TERMINAL_INVALID,
    EXEC_ERROR_ORIGIN_TRANSPORT: ReasonCatalog.TRANSPORT_ERROR,
}

# EXECUTE/VERIFY 轨迹事件 ``error_msg`` 截断上限(对齐 PLAN 闸门 ``_emit_plan_trajectory``
# 的 ``raw_msg if len(raw_msg) <= 500 else raw_msg[:497] + "..."`` 约定)。
_EXEC_ERROR_MSG_MAX = 500

# 固定流程兜底 mock 的"产出摘要"(节点真实上报未在 fallback 超时内闭环时,以此真实内容代替 [auto] 占位)。
# 取自 okr-implementation-relay 剧本(服装多平台大促)各节点"本跳产出正文"摘要,供下游 ## 上游产出正文 可读、
# 流程不因占位无意义文本而读不通。仅服务默认真实上报 + 80s 兜底路径;真实上报先到则本表内容不被使用。
_STATIC_MOCK_SUMMARY: dict[str, str] = {
    "marketing_strategy": (
        "双十一大促三平台差异化营销策略:淘宝预售蓄水(10.15起品类券)+11.11返场爆品,目标占GMV55%;"
        "京东近仓速达/价保/无忧退提客单,占25%;拼多多拉新组合+证质门槛,占20%。"
        "三平台统一主视觉、优惠规则前置公示。"
        "无法独立闭环:人群未分层、主推商品池未定需圈人/选品细化;多平台比价、低质拉新、负面舆情需风控评估。"
    ),
    "strategy_generation_group": (
        "完整大促营销策略(三平台差异化+人群分层+选品商品池+玩法):"
        "淘宝站内+直通车精准+品类券(满300减40),京东搜索/京准通+服务券(价保/速达),拼多多多多进宝拉新+门槛券;"
        "人群:淘宝老客分层(高价值/沉睡/流失召回)、京东品质人群+蓝海潜客、拼多多下沉新客设证质门槛;"
        "选品:淘宝冬装基本款+羽绒服爆款、京东高端羽绒+配饰、拼多多低价引流款设证质拦截刷单;"
        "玩法:预售10.15起定金翻倍、11.4–11.11品类券、11.11–11.15返场,总投入不超1000万。"
        "无法闭环:多平台比价、低质拉新、负面舆情需风控评估;舆情监测无人承接需安全架构师补。"
    ),
    "risk_lead": (
        "锁定8项重点风险点与评审范围:①券规则被中介套利(三平台品类券);②拼多多低质拉新二次客诉(下沉新客);"
        "③部分地区发货延迟(羽绒服重货);④客诉赔付超日常1.5倍;⑤京东价保争议;⑥热销缺货/预售超卖;"
        "⑦大促负面舆情无承接系统(无人承接,需安全架构师补舆情监测);⑧跨平台比价套利。建议风险评审群重点审②③④⑦。"
        "无法闭环:舆情监测无对接系统无承接团队,业务风控也补不了,带进风险评审群标无人承接/需安全架构师补。"
    ),
    "risk_assessment": (
        "8项风险逐项定级与约束:客诉赔付(高,赔付预案¥300万+夜间客服扩容)、发货延迟(高,热销品前置入仓+运力保底)、"
        "低质拉新(高,证质门槛+拉新黑名单)、券套利(中,单平台限购+实名校验)、价保争议(中,规则前置公示)、"
        "库存缺货(高,预售库存强校验)、跨平台比价(中,每日价差监控)、舆情监测(无人承接,转安全架构师)。"
        "无人承接任务:舆情监测系统无承接团队无对接系统→转BBS安全架构师承接。unhandled_tasks见上报硬字段。"
    ),
    "risk_unhandled_to_bbs": (
        "交付大促舆情监控补齐方案(舆情监测MVP):覆盖三平台店铺评论+社媒关键词,情感分类,负面按严重度分级,"
        "阈值触发告警→客服/运营闭环,接入审核/实施dashboard;MVP就位可供审核与实施接入。"
        "业务风险(逐项成因/等级/约束):低质拉新二次客诉(高,赔付预案¥300万+夜间客服扩容)、发货延迟(高,热销前置入仓+运力保底)、"
        "券套利(中,单平台限购+实名校验)、价保争议(中,规则前置公示)、库存缺货/预售超卖(高,预售库存强校验)、跨平台比价(中,每日价差监控)。"
        "技术/系统风险:券核销与库存并发须防超卖(强校验)、价差监控告警链路延迟、舆情监测接入告警通道联调。"
        "carry-forward上游全料:①三平台差异化营销策略 ②人群分层+选品商品池+玩法 ③上述风险逐项结论与约束,供下游审核一次审齐。"
    ),
    "strategy_approval": (
        "审核结论:批准有条件通过。问题清单:高—低质拉新二次客诉(证质门槛+黑名单,需实施盯控负向清单);"
        "高—发货延迟(热销前置入仓,需盯入仓率);高—客诉赔付(赔付预案¥300万+客服扩容,需盯赔付率);"
        "中—券套利(单平台限购);中—价保争议(规则前置);中—跨平台比价(每日监控)。舆情监测MVP就绪,实施接入告警通道。"
    ),
    "implementation": (
        "三平台投放配置单已定并挂监控:淘宝预售10.15起定金翻倍、品类券满300减40、11.11爆品返场,监控入仓率/赔付率;"
        "京东价保+速达服务券、搜索/京准通投放,监控价保工单;拼多多拉新组合+证质门槛+黑名单,监控低质拉新占比;"
        "跨平台每日价差监控;舆情MVP接入告警→客服。异常处置预案:触发阈值→降量/限购/赔付/公关。"
    ),
    "notify_done": (
        "大促方案已实施,通知负责人收尾。各跳交接摘要:①专家出三平台策略方向→②策略生成群细化人群+选品+玩法→"
        "③'风控圈8项风险点→③风险评审群评审定级与约束→④安全架构师补舆情监测MVP+带全料→⑤审核批准有条件通过+三项硬约束→"
        "⑥落地配置单+监控+预案。当前状态:已实施待执行日,三项高风险需盯控(低质拉新/发货延迟/客诉赔付),舆情监测已上线。"
    ),
    # ---- merchant-operations-goal-to-plan 节点 mock(理发店18周年店庆) ----
    "set_redlines": (
        "18周年店庆任务单(全套公共约束):活动窗口2026.10.15-11.15(32天)、营业时段10:00-22:00、促销预算≤20万、备货现金占用≤8万、价格红线(补贴率≤30%、最低折扣85折、护理套餐最低价298)、平台合规红线(券有效期/预约限制须明示、禁止全城最低)、客流目标(新客1500/护理200)。自持单品成本、毛利底线、授权清单留给利润核算。发门店运营测产能。"
    ),
    "ops_capacity": (
        "门店能力画像(运营子集测算):剪发位8、护理位3、技师12(剪发8/护理4);剪发45min、护理90min;周末剪发新增约15人/天、护理新增约8人/天(扣自然客流),工作日余量较足;可扩:临时剪发技师+2→剪发+10人/天、延营至23:00→周末+5人/天;护理耗材现有30份、备货8万可补到60份;目标新客1500(≈47人/天)需扩产能可达。营销约束原样透传:促销预算≤20万、价格红线、平台合规红线、客流目标(不解读不使用)。交营销出方案。"
    ),
    "marketing_plan": (
        "营销方案(能力内):新客王牌剪发体验券原价68→体验价48(补贴29%≤30%),按产能分批投放冲1500;老客头皮+基础护理套餐90min、398(≥298红线),首批按库存60份与护理位产能限量、预约制;会员周年双倍积分+会员日提前购;节奏:预约占用率>75%暂停放量。送评审群审。"
    ),
    "review_group": (
        "评审群结论(商场运营+线上平台两路,默认给修订条件不打回):商场侧——展位报批通过、传单限入口、营业时段内执行;平台侧——券有效期与预约限制须在活动页明示、禁止全城最低、设等待超时安抚、客诉超阈值暂停投放。过审带修订条件,交店主利润核算。"
    ),
    "profit_accounting": (
        "利润核算表(自持成本):体验券单次成本25/售价48达标;护理套餐成本180/售价398达标;某引流款低于毛利底线标红。授权清单:体验券分批投放可自主、护理套餐放量需店主审批、扩产能(临时技师)需店主审批。待取舍:低价引流款是否保留、临时技师成本是否接受。交投放实施。"
    ),
    "launch": """# 理发店「十八而立 · 焕新一剪」18 周年店庆营销方案

> 用「老客户的第 18 年 × 新客的第一次」双线叙事,把店庆做成一场有情怀、有转介绍、能沉淀会员的增长战役——32 天一条「引流→体验→复购→裂变」自循环增长飞轮。
>
> 活动窗口 2026.10.15–11.15(32 天)｜ 促销预算 ≤ 20 万｜ 备货新增占用 ≤ 8 万

## 〇 执行摘要:为什么这套方案能打动人

| 常见店庆做法 | 本方案不同之处 |
| --- | --- |
| 发券拉满、价格战、伤老客 | 以「老客第 18 年 × 新客第 1 次」双线叙事,让老客感到被尊重、新客感到被欢迎 |
| 核销靠天、转化断层 | 设计到店体验剧本打通「核销→护理→会员」三段转化,把 15% 转化率做成可执行标准 |
| 拉新一次性、走完即散 | 内建老客带新客裂变环,让每个新客背后站着一位老客推荐人,沉淀可复购会员 |
| 只看销量不看口碑 | 专设老客口碑护城河:老客专属权益 + 投诉快通道,护住「不伤老客」这条 O |

**期望效果**:体验券核销 ≥ 1000 → 新客到店 ≥ 1500(核销 + 裂变 + 自然)→ 护理转化 ≥ 15% 配额 200 份 → 到店授权会员 ≥ 800。四条 KR 一条漏斗串通,而非各自为政。

## 一 战略主张:十八而立,焕新一剪

- **品牌叙事**:18 周年 = 一把剪刀用 18 年,剪出新老两代人的体面。老客在这里的 18 年值得被看见,新客的第一剪值得被记住。
- **双线定位**:对老客——「陪你第 18 年」,专属权益 + 回忆杀,把忠诚换成转介绍和复购;对新客——「你的第一次,交给我们」,低门槛体验卡 + 到店即被重视,第一印象即转化入口。
- **核心策略**:不做价格战,做体验战——把 48 元体验价定位为「见面礼」而非折扣,把 398 元护理套餐定位为「升级仪式」,把会员定位为「加入 18 周年朋友圈」。

## 二 增长模型:一条漏斗串起四条 KR

```
曝光(社群/传单/路人) → 领券 → 到店扫码核销 → 护理升级 → 会员授权 → 老客裂变带新
```

| 漏斗环节 | 目标 | 转化口径 | 拆解逻辑 |
| --- | --- | --- | --- |
| 体验券投放 | 1800 张(分批) | — | 首批 50 试水,按产能放量 |
| 券核销 | ≥ 1000 张 | 核销率 ≥ 55% | 到店扫码 + 领券去重保障口径 |
| 新客到店 | ≥ 1500 人 | — | 核销 1000 + 裂变约 300 + 自然/路过约 200 |
| 护理售卖 | ≥ 200 份 | 转化率 ≥ 15% | 从体验客基数约 1300 引导,约 15.4% 命中 |
| 新增会员 | ≥ 800 人 | 授权率约 55% | 到店即授权 + 双倍积分诱因 |

四条 KR 不是平行罗列,而是一条漏斗逐级转化:核销 1000 自然推动新客 1500,护理 200 自然推动会员 800,目标之间互相成就。

## 三 三大主场活动(产品力设计)

### 3.1 「初见卡」——新客王牌体验券
- 原价 68 → **体验价 48**(补贴 29% ≤ 30% 红线),成本 25,毛利 23 达标。
- 命名「初见卡」:不是打折券,是「第一次见面的诚意」。
- 分批按产能放量,首批 50 张试水;预约占用率 > 75% 暂停,护住体验质量。
- 渠道:线上社群 + 线下传单同步,扫码领券、到店核销。

### 3.2 「焕新护理套餐」——老客升级仪式
- **398 元**(≥ 298 红线),90 min,成本 180,毛利 218 达标。
- 命名「焕新」:从一次剪发升级为「头皮 + 基础护理」的仪式感,承载 15% 转化率。
- 预约制 + 库存 60 份限量,稀缺感促即时决策。

### 3.3 「18 周年朋友圈」会员俱乐部——沉淀可复购
- 周年**双倍积分** + 会员日(每周三)提前购。
- 入会即赠「18 周年纪念券」,锁定二次到店。
- 到店即引导实名授权,目标新增会员 ≥ 800,把流量变成可复购资产。

## 四 客群分层与触达:四类人四套话

| 客群 | 占位 | 触达与权益 | 目的 |
| --- | --- | --- | --- |
| 忠诚老客 | 转介绍主力 | 专属权益 + 老客优先迎接 + 转介绍双倍奖励 | 换复购与裂变 |
| 沉睡老客 | 召回对象 | 「第 18 年,老位置还给你留着」召回消息 + 回归体验券 | 唤醒回店 |
| 新客 | 增量来源 | 初见卡 48 元见面礼 + 到店重点服务引导 | 转化为核销与会员 |
| 转介绍新客 | 高质量增量 | 老客带新客,新客享初见卡、老客得「伯乐券」 | 裂变环放大 |

**裂变环**:每位老客推荐 1 名新客,老客得「伯乐券」(下次护理立减),新客得初见卡优先时段——让拉新从门店单向发券,变成老客主动带来,质量与口碑双升。

## 五 32 天作战节奏:四幕情感弧

| 幕 | 日期 | 主题 | 关键动作 | 控流 |
| --- | --- | --- | --- | --- |
| 第 1 幕 · 蓄水 | 10.15–10.19 | 「第 18 年,回来了」 | 老客召回 + 首批初见卡 50 张试水 + 社群情怀预热 | — |
| 第 2 幕 · 引爆 | 10.20–11.05 | 「你的第一次,交给我们」 | 初见卡放量 + 焕新护理开放预约 + 转介绍启动 | 占用率 > 75% 暂停 |
| 第 3 幕 · 高潮 | 11.06–11.11 | 「周年朋友圈,一起焕新」 | 会员日早购 + 满额赠 + 高峰盯产能客诉 | 客诉触发暂停 |
| 第 4 幕 · 沉淀 | 11.12–11.15 | 「再见,也是老朋友」 | 返场复购券 + 会员二次邀请 + 复盘收官 | — |

## 六 到店体验剧本:把转化率做成标准动作

> 很多店庆死于「核销完就走」,本方案把到店 45 分钟设计成转化流水线。

1. **迎接(0–2 min)**:扫码核销 + 递「18 周年欢迎卡」;新客/老客话术分流,老客说「老位置还在」,新客说「第一次交给我们」。
2. **服务(2–35 min)**:剪发中由技师自然种草焕新护理(基于头皮/发质一句点评),不硬推销。
3. **升级(35–40 min)**:体验后出示「焕新护理限时预约」,引导预约即享会员价。
4. **入会(40–43 min)**:实名授权入「18 周年朋友圈」,即赠纪念券锁定二次到店。
5. **离店回访(44–45 min + 当晚)**:加企业微信,当晚发护理预约提醒 + 转介绍「伯乐券」入口。

15% 护理转化率与 55% 会员授权率,由这五步标准动作承担——不是赌手感,是按剧本走。

## 七 老客口碑护城河(守 O:不伤老客)

| 动作 | 作用 |
| --- | --- |
| 老客专属权益(双倍积分 + 会员日提前购 + 伯乐券) | 让老客感到「18 年被看见」,主动复购与转介绍 |
| 老客优先时段与迎接 | 高峰期不与抢券新客挤占,护住体验质量 |
| 投诉快通道(24h 响应 + 赔付预备金) | 客诉 > 日常 1.2 倍即触发暂停投放,第一时间止损 |
| 不做全城最低价、不价格战 | 避免老客「买贵了」的心理落差,守住信任 |

## 八 产能与扩容(增长落地的硬底盘)

- 剪发位 8 / 护理位 3 / 技师 12(剪发 8·护理 4);剪发 45 min、护理 90 min。
- 周末产能余量:剪发 +15 人/天、护理 +8 人/天(扣自然客流);工作日余量足,承接核销。
- 护理耗材现有 30 份,备货补到 60 份。
- **扩容(需店主审批)**:临时剪发技师 +2 → +10 人/天;延营至 23:00 → 周末 +5 人/天。高峰前到位,避免「来了接不住」伤口碑。

## 九 价格与利润核算

| 品项 | 成本 | 售价 | 毛利 | 判定 |
| --- | --- | --- | --- | --- |
| 初见卡(体验券) | 25 | 48 | 23 | ✅ 达标 |
| 焕新护理套餐 | 180 | 398 | 218 | ✅ 达标 |
| 低价引流款 | — | — | — | ❌ 低于毛利底线,标红待取舍 |

**授权划界**:初见卡分批投放 = 自主执行;护理放量 / 扩产能临时技师 = 需店主审批;低价引流款 = 待取舍。

## 十 预算与备货

**促销预算 ≈ 11 万(留 9 万缓冲,远低于 20 万上限)**

| 科目 | 万元 |
| --- | --- |
| 初见卡减收(68→48,按核销 1000 计) | 2.0 |
| 物料(传单/展位/易拉宝/欢迎卡) | 1.5 |
| 线上社群推送 + 内容制作 | 0.8 |
| 会员权益(双倍积分 + 会员日早购 + 纪念券) | 2.5 |
| 扫码核销 + 领券去重配置 | 1.0 |
| 客诉赔付预备金 | 1.0 |
| 应急机动 | 2.2 |
| 临时技师工时(需审批) | 1.0 |

**备货现金占用 ≈ 1–2 万(远低于 8 万)**:护理耗材 30 → 60 份(增量 30 × 180 ≈ 0.54 万)+ 物料备货。

## 十一 风险与护航

| 风险 | 阈值 | 处置 |
| --- | --- | --- |
| 重复领券 | 同 ID 多领 / 核销异常 | 扫码核销 + 领券去重拦截,必要时暂停该批次 |
| 产能不足 | 占用率 > 75% | 暂停放量,启临时技师(需审批) |
| 老客被冷落 | 老客投诉上升 | 启用老客优先时段 + 快通道赔付 |
| 核销滞后 | 日核销 < 20 | 调整投放时段 / 返场加推 |

## 十二 KPI 作战看板(每日刷新)

| 指标 | 目标 | 盯控频次 |
| --- | --- | --- |
| 新客到店 | ≥ 1500 | 日 |
| 券核销 / 核销率 | ≥ 1000 / ≥ 55% | 日 |
| 护理售卖 / 转化率 | ≥ 200 / ≥ 15% | 日 |
| 新增会员 / 授权率 | ≥ 800 / 约 55% | 日 |
| 客诉差评 | ≤ 日常 × 1.2 | 滚动 |
| 转介绍新客数(裂变环健康度) | ≈ 300 | 日 |

## 十三 终态验收与复盘

- ✅ **达成**:六项 KR 全部完成,预算未超、口碑守住。
- ⚠️ **部分达成**:列出未达项与补救(如护理转化不足则加推体验包)。
- ❌ **未达成**:预算超支或客诉失控,启动复盘定责。

**收官复盘**:预算核销差异、会员二次到店率、转介绍裂变系数、护理复购率——把这次店庆沉淀为下一年可复制的增长模板。
""",
}

# 固定流程兜底 mock 的"不可实现任务"(risk_assessment 上报无结构化 unhandled_tasks 时兜底)。
# 大促剧本:风险评审认为差外部舆情监控能力,内部找不到对应舆情监控 bot → 转 BBS 广场由安全架构师承接。
_UHT_MOCK: list[dict[str, str]] = [
    {
        "id": "uht-sentiment-1",
        "title": "舆情监控方案缺失",
        "reason": "风险评审认为差外部舆情监控能力(发货慢/尺码偏小等负面舆情无监测,需告警→客服闭环),内部找不到对应舆情监控 bot,转 BBS 广场由安全架构师承接",
    },
]

# 陈旧飞行态阈值(dispatching=True 超此即视为崩溃遗留,redrive 可清理重派)。
# 默认 60s:正常 start_run 在途远小于此;与默认 recovery lease(60s)对齐——崩溃任务经 recovery
# 拾起时 dispatching_at 已超阈值,判陈旧;新鲜在途派发保留,不与 redrive 双派发。可经 env 调。
_DISPATCHING_STALE_SECONDS = int(os.environ.get("OCB_DISPATCHING_STALE_SECONDS", "60"))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_dispatch_side_data(graph, task_id: str, node_id: str) -> tuple[dict | None, int]:
    """Read the DISPATCH trajectory gate's side data — ``_dispatch_rationale`` carrier
    (REQ-2 #3) and harness-retry attempt — in **one graph query** for one node.

    Returns ``(ext_info_ready_dict_or_None, attempt_int)``:
        * ``ext_info_ready_dict`` = ``{"_dispatch_rationale": <rat_dict>}`` (the
          gate-ready ``ext_info`` payload) when the rationale is present, else
          ``None`` (the gate then emits with ``ext_info=None``).
        * ``attempt_int`` = ``int(node.run_info.extend_props.get("harness_retries", 0) or 0)``.

    ``TaskDispatcher.dispatch`` writes ``dataclasses.asdict(DispatchRationale)`` into
    ``node.run_info.extend_props["_dispatch_rationale"]`` (no contextvar exists —
    extend_props is the carrier). ``query_task_dashboard`` returns the same in-memory
    objects the dispatcher mutated, so the rationale is available even when the engine
    re-queries the graph after dispatch (``update_task_node_info`` issues
    ``extend_props.update(...)`` which preserves unknown keys).

    Defensive: any failure (graph raise / missing node / unparsable) → ``(None, 0)``;
    the trajectory gate never blocks the forward-driving path (decision #14 swallow
    guarantee). Used by the MISS gate (its ``patch`` is a ``TaskNodePatch``, not a
    node — it must re-query). The HIT_SINGLE / HIT_MULTI gates keep inlining the
    rationale+attempt from the in-scope in-memory ``cur`` / ``node`` (no extra query).
    """
    try:
        node = next(
            (n for n in graph.query_task_dashboard(task_id).tasks if n.node_id == node_id),
            None,
        )
        if node is None:
            return None, 0
        rat = node.run_info.extend_props.get("_dispatch_rationale")
        fail = node.run_info.extend_props.get("_dispatch_failure")
        ext_info: dict[str, Any] | None = None
        if isinstance(rat, dict) or isinstance(fail, dict):
            ext_info = {}
            if isinstance(rat, dict):
                ext_info["_dispatch_rationale"] = rat
            if isinstance(fail, dict):
                # 降级备注(rationale 装配失败 / 序列化失败):随 DISPATCH hit/miss 事件落 ext_info
                # 可见性备注 —— 非 error_type,analyzer failure_reason 不据此派生(decision #14 兼容)。
                ext_info["_dispatch_failure"] = fail
        attempt = int(node.run_info.extend_props.get("harness_retries", 0) or 0)
        return ext_info, attempt
    except Exception:  # noqa: BLE001  trajectory 旁路读取,失败 → (None, 0),不阻塞闸门
        return None, 0


def _dispatch_fail_action_result(derr: str | None) -> str:
    """Map a ``dispatch_error`` state-flag value to a trajectory ``action_result``
    for the DISPATCH-failed gate. The short ``dispatch_error`` string is a harness
    routing flag (e.g. ``dispatch_exception:TimeoutError`` / ``no_result``); this
    normalizes it to an open-ended ``action_result`` token for the trajectory row
    (the precise value still rides in ``error_msg``). Unknown → ``"failed"``.
    """
    d = (derr or "").strip()
    if d.startswith("dispatch_exception"):
        return "dispatch_exception"
    if d == "no_result":
        return "no_result"
    return "failed"


def _is_stale_dispatching(node: "object") -> bool:
    """``dispatching=True`` 是否崩溃遗留的陈旧飞行态(redrive 清理判定用,纯 timestamp)。

    有 dispatching_at 且在途阈值内 → 新鲜在途派发(不清,否则与 start_run 双派发);
    无 dispatching_at(改动前数据)或超阈值 → 陈旧(崩溃遗留)→ 可清。dispatch_error 不在此处理
    (harness 自有重试链,不靠 redrive 清)。"""
    ep = node.run_info.extend_props
    if not ep.get("dispatching"):
        return False
    at = ep.get("dispatching_at")
    if at is None:
        return True
    return (_now_ms() - int(at)) >= _DISPATCHING_STALE_SECONDS * 1000


@dataclass(frozen=True)
class CoopGroupStart:
    """Result of starting a BCN coop group: the group id + its initial session_id."""

    group_id: str
    session_id: str | None
