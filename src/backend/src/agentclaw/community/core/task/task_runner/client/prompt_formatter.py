"""默认 PromptFormatter + _RunnerContextBuilder。

零 case:仅消费 _build_context dict 字段(mode/node_instruction/goal/...) + node.task_spec,不写节点名。
"""
from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.task.domain.prompt_constants import (
    NO_WEB_SEARCH_CONSTRAINT,
    OUTPUT_LANGUAGE_CONSTRAINT,
)

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_runner.client.ports import (
    PromptFormatter, TaskContextBuilder,
)


def _skill_report_instruction(context: dict[str, Any], *, task_id: str, node_id: str) -> str:
    """skill HTTP callback protocol block for task-node result reporting.

    Only emitted when ``skill_report_enabled`` is true. The disabled path must
    not describe the platform's pull protocol to the model; the platform owns
    result collection in that mode.
    """
    backend = str(context.get("backend") or "{backend}")
    reporter = context.get("reporter_bot_id")
    reporter_line = (
        f"唯一上报者: reporter_bot_id={reporter}; reporter_role={context.get('reporter_role') or 'worker'}。"
        if reporter
        else "当前执行 Bot 是唯一上报者。"
    )
    return "\n".join([
        "【强制执行闭环，不得跳过】",
        "产出为本:本节点的全部价值=阶段1 在本条消息正文给出的真实业务产出;阶段2-4 只是对该产出的校验与回投,不是任务本身。"
        "若阶段1 正文没有可直接阅读的真实业务内容(只见标题/阶段名,或内容被外置到工具写文件/写代码里而不在正文),"
        "则整轮作废,不得进入阶段2-4,更不得靠自我背书的空壳去抢上报——那是无产出的假闭环。",
        "执行约束:严禁调用联网搜索/web_search 工具或联网浏览外部资源获取信息(任务指定的上报接口除外),仅依据给定上下文与自身知识产出。",
        "阶段1 执行：在本条消息正文完整完成上面任务指令、给出完整执行产出(完整分析/结论/内容正文),正文必须可直接读到真实业务内容;"
        "禁止:①只写小标题或阶段名(如 阶段1:完成分析文档)就跳过;②仅靠调用工具(写文件/写代码/写表格)后即视为完成而正文无内容;"
        "③把产出只放进后续上报请求体却不出现在对话正文。产出完整后再进入阶段2。阶段4 上报时把这份完整产出原样填入 output 字段。",
        "阶段2 校验：执行完成后，必须逐条对照当前 goal.acceptances，明确判断每条是否满足；不能只凭‘看起来完成’结束。",
        "阶段3 验收：整理完整 output；验收全部满足则 status=SUCCESS，否则 status=DONE，并在 acceptance_result.gaps 写明差距；只有执行失败才使用 status=FAILED。",
        "阶段4 上报：必须真正发起 HTTP POST，不能只在对话中输出‘完成’或只返回 JSON。",
        reporter_line,
        f"回调地址: POST {backend}/api/v1/collaboration/tasks/callback/report",
        "请求体只能包含以下节点级字段，不要增加 loop_task_id/workflow_type/workflow_id/instance_id/result 包装：",
        json.dumps({
            "task_id": task_id,
            "node_id": node_id,
            "status": "SUCCESS",
            "output": "完整执行输出",
            "acceptance_result": {
                "verdict": "DONE",
                "acceptances_metric": [
                    {"id": "验收项ID", "passed": True, "summary": "满足原因"}
                ],
                "gaps": [],
            },
            "extend_props": {},
        }, ensure_ascii=False),
        "上报前自检：task_id/node_id 必须使用当前节点值；status 只能是 SUCCESS(验收通过)/DONE(验收未通过)/FAILED(执行失败)；验收通过时 verdict=DONE 且 gaps=[]，验收未通过时 verdict=FAILED 且 gaps 非空；acceptances_metric 必须是数组，不要改成 {验收项ID:{passed,summary}} 映射；收到 HTTP 200 前不得认为上报完成。",
        "收尾轮约束:收到 HTTP 200 即视为本节点上报完成;此后回复只能是**一句话确认**(如 已上报成功),严禁在收尾轮再次贴出阶段1执行产出、验收正文、上报请求体或任何此前已产出的内容——执行产出只在阶段1给出一次,不得重复。",
        "上报工具与方法:上报只能用 exec/curl 以 JSON body 发起 POST 到回调地址;严禁用 web_fetch / web_search 等工具调用上报接口(它们是 GET/检索,会 405);收到 HTTP 200 立即停止,不得对同一节点重复 POST、不得换工具重试、不得在 200 之后再发起任何上报或收尾调用。",
    ])


def _no_callback_instruction() -> str:
    """Boundary-only instruction for platform-managed result collection.

    Deliberately omits the platform's pull payload schema so the model does
    not confuse an internal collection format with business output.
    """
    return "本节点结果由平台接口负责回收；不要主动调用 /callback/report，不要构造或发送节点级 Push 请求。"


def _static_relay_closure() -> str:
    """Static-plan relay closure instruction.

    Static-plan node results are collected by the platform (StaticPlanRuntime
    fallback report), so the bot must NOT call the report interface or send
    node-level pushes. Web search stays banned, but the shared
    NO_WEB_SEARCH_CONSTRAINT is not reused verbatim because it mandates calling
    the report interface. Wording avoids any 'mock'/demo marker so the bot's
    visible execution output reflects genuine relay capability.

    For collaboration groups: the driver runs a multi-round relay — (1)
    take-over round, (2) dispatch + every party incl. driver-as-worker produces
    its own member-level output, (3) aggregate-and-hand-off round ONLY after ALL
    members have replied. The group final execute-output + gap/hand-off appear
    only in the last round, never prematurely in intermediate rounds.
    """
    return chr(10).join([
        '【执行闭环·三步缺一不可,顺序为承接→执行→交接】',
        '严格按上方【接力执行】顺序逐步完成,不得跳过、合并或只用一句话概括代替:',
        '- 接力上下文:承接时复述接自谁、上游规划交你执行的下一步任务是什么、你的承接边界;',
        '- 执行产出:真正推理完成本节点任务指令,给出完整执行结论与产出,不得只复述任务不产出;',
        '- gap与交接:获取上方最新统一上下文(上游产出正文+本步产出),自己对照目标计算 gap、规划下一步任务,并在正文中写明下一步交给谁、做什么;本链路终点节点则在此声明接力终结、不再派发。',
        '【协作群多轮接力(仅当上方【## 群组成】列出除你之外的其它 Bot 成员)】三步按多轮展开,承接→中间执行→收齐汇总三轮缺一不可,不得合并成单轮;你是 driver(协调者)时按 1-3 逐轮执行,你是被派发成员(consultant 等)时只做第2步各方执行:',
        '1) 承接轮(driver 先单独回复):复述接自谁、上游交你的下一步任务、你的承接边界,并说明本群各成员 Bot 分工(含 driver 自己的职责);此轮只发承接,不派发、不自评、不产出任何执行结论,更不得给出 gap 与交接。',
        '2) 派发 + 各方执行轮(driver):对【## 群组成】中每个非人类 Bot 成员用 bcs_assign_task(主从群)或 bcs_route(chat 群)派发;派发消息必须把上方【## 上游产出正文】全文复制进去,并在消息内声明“此即本次完整输入,不得请求更多详情”;human 仅为观察者,不得向 human 派发;driver 自己也是执行者,须并行完成自己职责范围内的产出,不得把全部执行甩给成员。',
        '被派发成员:上方【## 上游产出正文】及派发消息带入的正文即为完整输入,视为已提供全部所需上下文;无论详略,立即基于此 + 自身合理假设给出你职责内的完整审查结论或修订条件;严禁以“缺详情/需完整方案”为由请求追加输入、要求同步方案、暂停或拒绝执行——一律直接产出结论。',
        '本轮各方给出的(含 driver 自评)都只是成员级产出,不是本协作群最终执行产出;本轮不得做 gap 计算、不得给出交接;driver 派发完毕即结束本轮,等待成员回复,不得在同一轮强行追加汇总或交接。',
        '3) 收齐汇总与交接轮(driver):只有当所有成员(含 driver 作 worker)都已在本群回复各自产出后,driver 才进入此轮;综合全部成员产出与自己已完成产出 = 本协作群执行产出(即“执行产出”步),并对照目标计算 gap、规划下一步任务(即“gap与交接”步),在正文描述下一步交给谁、做什么,不路由群外;此轮是本协作群的最终输出——执行产出与 gap交接只在此轮给出,承接/派发/自评/成员回复等中间轮一律不得提前给出执行产出与 gap交接。',
        '判定收齐:群内每个非人类成员均已产出其职责结论后即视为收齐,driver 立即进入汇总轮;若仍有成员未回复,继续等待,不得提前收尾或替未回成员编造意见。',
        '此轮只综合成员回复成新结论,不重贴承接段与成员原文。',
        '【单人接力(无【## 群组成】或仅你一人)】上述三步一次性在正文中给出完整产出,不得只复述任务不产出。',
        '【交接只描述,不路由群外】交接部分的下一步派发仅在正文描述;真正的节点间派发由平台/workflow 引擎完成;禁止用 bcs_route 或 bcs_assign_task 路由给【## 群组成】之外的 bot(群外下一节点);也不要调用 bcs_task_complete / bcs_fuse 等收尾工具(本节点结果由平台统一回收)。',
        '【正文不重复输出】不要把你已给出的承接/产出在轮末二次重述、或作为 summary 收尾再发一遍;协作群汇总阶段只综合成员回复成新结论,不重贴你的承接段。',
        '输出要求:不要用 step 编号或第N步式序号作小标题机械罗列三步;正文须保留 markdown 排版——用标题、段落、列表、表格、加粗等结构把承接、产出与交接组织清晰、分区呈现,保持专业层级与可读性,读起来是结构化的专业产出,而非一段扁平纯文本或分步打卡清单。',
        '执行约束:禁止调用联网搜索/web_search/联网检索工具或浏览外部网页、外部 API 等外部网络资源获取信息,仅依据上方给定上下文与自身知识产出结论。',
        '本节点执行结果由平台统一回收,无需你主动调用上报接口或构造节点级 Push;你在群内给出完整执行产出即可,产出内容只呈现本步执行结论与下一步交接。',
    ])


class PromptFormatterImpl(PromptFormatter):
    def format_execute(self, context: dict[str, Any], node: TaskNode) -> str:
        instr = context.get("node_instruction") or node.task_spec.metadata.instruction
        # 接力交接节点:instruction 由 static_plan_runtime._decorate 注入 "# 接自 ..." 三段
        # (# 接自 / ## 群组成 / ## 上游产出正文 / ## 本角色任务)。此时直接下发交接正文即可——
        # bot 收到的是"从 X 接过来一个任务,情况是…",不再套派单/目标/验收/回收协议/字段要求/禁联网;
        # 结果回收与验收由各 bot 的 skill/rule 和平台回收机制承托。
        if str(instr).lstrip().startswith("# 接自"):
            # static_plan 接力交接:节点结果由平台统一回收(StaticPlanRuntime 兜底回投),
            # 不在 prompt 注入 HTTP 上报协议/回调地址/请求体,避免 bot 真去调 /callback/report。
            # 仅保留接力执行约束(禁联网,但不强制上报)+ 平台回收声明 + 中文输出;
            # 措辞不出现 mock/演示等字眼,产出体感为真实接力能力。
            return f"{instr.rstrip()}\n{_static_relay_closure()}\n{OUTPUT_LANGUAGE_CONSTRAINT}"
        goal = node.task_spec.goal.objective
        siblings = context.get("sibling_outputs") or {}
        acceptances = [
            {"id": acceptance.id, "description": acceptance.description}
            for acceptance in node.task_spec.goal.acceptances
        ]
        parts = [
            "[task-execute]",
            NO_WEB_SEARCH_CONSTRAINT.rstrip(),
            OUTPUT_LANGUAGE_CONSTRAINT,
            "请严格按以下阶段执行，执行、校验、验收、上报均不可跳过。",
            f"目标:{goal}",
            f"指令:{instr}",
            f"验收标准:{json.dumps(acceptances, ensure_ascii=False)}",
        ]
        # 所有执行模式统一由 skill_report_enabled 决定是否主动 callback；
        # 关闭时只告诉 Bot 不要主动 callback，平台内部回收格式不注入 prompt。
        if context.get("skill_report_enabled", True):
            parts.append(_skill_report_instruction(
                context,
                task_id=str(context.get("task_id") or node.task_id),
                node_id=str(context.get("node_id") or node.node_id),
            ))
            parts.append("HTTP 上报完成后，回复中只需确认上报结果；不要用回复文本替代 HTTP POST。")
        else:
            parts.append(_no_callback_instruction())
        if siblings:
            parts.append(f"上游产出:{json.dumps(siblings, ensure_ascii=False, default=str)}")
        parts.append(NO_WEB_SEARCH_CONSTRAINT)
        return "\n".join(parts)

    def format_verify(self, context: dict[str, Any], node: TaskNode) -> str:
        child_outputs = context.get("child_outputs") or {}
        acceptances = context.get("acceptances") or []
        acc = ";".join(a.description for a in acceptances)
        return f"验收标准:{acc}\n子产出:{child_outputs}\n\n{NO_WEB_SEARCH_CONSTRAINT}"


class _RunnerContextBuilder(TaskContextBuilder):
    def __init__(self, runner) -> None:
        self._runner = runner

    def build(self, task_id: str, node_id: str) -> dict[str, Any]:
        return self._runner._build_context(task_id, node_id)  # integration 内聚访问
