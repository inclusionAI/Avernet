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

from agentclaw.community.core.task.domain.models import TaskNode, task_spec_instruction
from agentclaw.community.core.task.task_runner.client.ports import (
    PromptFormatter,
    TaskContextBuilder,
)


def format_task_node_business_instruction(
    *,
    task_id: str,
    node_id: str,
    backend: str,
    objective: str,
    instruction: str,
    acceptances: list[dict[str, Any]],
    upstream_outputs: dict[str, Any] | None = None,
    reporter_bot_id: str = "",
    executor_bot_ids: list[str] | None = None,
    skill_report_enabled: bool = True,
    relay_execution: bool = False,
    relay_blackboard: dict[str, Any] | None = None,
) -> str:
    """Build the single per-node protocol used by runner delivery.

    Relay tasks use one event-driven baton protocol. Centralized nodes use the
    original terminal callback protocol, because their graph completion is
    owned by the task engine rather than the next bot.
    """
    backend = backend.rstrip("/") or "{backend}"
    reporter = reporter_bot_id or "BCS 系统上下文标识为 manager/driver 的 Bot"
    executors = executor_bot_ids or ([reporter_bot_id] if reporter_bot_id else [])
    callback = f"{backend}/api/v1/collaboration/tasks/callback/report"
    context = f"[task-loop] loop_task_id={task_id}::{node_id}; backend={backend}"

    if relay_execution:
        execution_payload = {
            "task_id": task_id,
            "node_id": node_id,
            "event_type": "EXECUTION_RESULT",
            "event_id": "每次事件使用新的 UUID",
            "holder_id": reporter,
            "progress_reason": "为什么当前事实可以被记录",
            "failure_reason": None,
            "payload": {
                "execution_decision": "ACCEPTED",
                "actual_goal": {
                    "objective": "能力匹配后实际执行的 goal",
                    "acceptances": [{"id": "验收项ID", "description": "验收要求"}],
                },
                "output": {"result": "完整执行产出"},
                "acceptance_result": {
                    "verdict": "DONE 或 FAILED",
                    "acceptances_metric": [
                        {"id": "验收项ID", "passed": True, "summary": "证据摘要"}
                    ],
                    "gaps": [],
                },
            },
        }
        declined_payload = {
            "task_id": task_id,
            "node_id": node_id,
            "event_type": "EXECUTION_RESULT",
            "event_id": "每次事件使用新的 UUID",
            "holder_id": reporter,
            "progress_reason": "能力准入结论",
            "failure_reason": "capability_mismatch",
            "payload": {"execution_decision": "DECLINED"},
        }
        return "\n".join(
            [
                "[task-execute]",
                "【分布式接力闭环】这是本棒唯一执行与上报协议；严禁回到中心化的 status/output/acceptance_result 节点终态回调，也严禁把 EXECUTION_RESULT 成功当作本棒结束。",
                context,
                f"唯一闭环持有者 holder_id: {reporter}。协作群成员完成分工产出后，driver/manager 汇总、验收并驱动本棒闭环；其它成员不调用任务接口。",
                f"本群执行者: {json.dumps(executors, ensure_ascii=False)}",
                f"目标:{objective}",
                f"指令:{instruction}",
                f"验收标准:{json.dumps(acceptances, ensure_ascii=False)}",
                f"上游产出:{json.dumps(upstream_outputs or {}, ensure_ascii=False, default=str)}",
                f"共享任务黑板:{json.dumps(relay_blackboard or {}, ensure_ascii=False, default=str)}",
                "固定阶段编号 S1-S8：S1读取上下文、S2计算GAP、S3能力匹配、S4执行并上报、S5更新GAP、S6解析下一棒、S7搜推并指定执行者、S8实际交接。向会话透出阶段明细必须使用【S1/8 ...】到【S8/8 ...】连续标签；S2/S3/S5/S6是本地推理，不得调用上报接口，也不得跳号；同一阶段内的重试必须保留同一个 S 编号。",
                "最小调用与输出契约：正常有GAP链路最多6次HTTP调用：1次context、3类必要事实上报EXECUTION_RESULT/PLAN_RESULT/DISPATCH_RESULT、1次search、1次dispatch；无GAP链路不得调用search、DISPATCH_RESULT或dispatch。Skill加载、协议阅读、工具调用和同event_id重试都不是新步骤；每个阶段最多输出一行事实结论，禁止步骤0、步骤0确认、S2-S3合并编号或协议章节号。",
                f"S1/8 解析任务最新上下文：GET {backend}/api/v1/collaboration/tasks/{task_id}/context，保存 TaskContext(spec, all_done_output, gaps)。本棒只做当前节点，不修改任何前序节点或根节点运行事实。",
                "S2/8 计算当前GAP：在本地对齐根 spec.goal.acceptances、all_done_output 的实际目标/最终产出/节点验收事实和既有 gaps，推理当前仍未覆盖的根任务范围；本阶段不调用 HTTP 上报接口。",
                "S3/8 Bot能力匹配：输入本地 TaskContext、当前 TaskNode.task_spec、IDENTITY.md 职责、已激活 Skills 和工具真实可用状态，输出实际可执行的 actual_goal 或零覆盖 DECLINED。职责覆盖和工具/Skill事实覆盖必须同时成立；通用模型知识、搜索或抓取工具不能扩大职责边界。本阶段不调用 HTTP 上报接口。",
                "S4/8 任务执行并统一上报：只执行 actual_goal；driver 汇总协作群分内产出，逐条形成节点级验收事实；执行完成后再一次性上报 EXECUTION_RESULT。零覆盖时不生成业务产出，上报严格 DECLINED。报告/文档类交付物必须把完整 Markdown 全文放入 payload.output.result。",
                f"POST {callback} 上报 EXECUTION_RESULT；event_id 必须新生成。ACCEPTED 请求体示例：",
                json.dumps(execution_payload, ensure_ascii=False),
                "DECLINED 请求体示例如下；DECLINED payload 只能携带 execution_decision，不得携带 actual_goal、output、acceptance_result 或 gaps，不可伪造业务产出，原因写入 failure_reason：",
                json.dumps(declined_payload, ensure_ascii=False),
                "响应 data.relay_turn 是后续 PLAN/搜索/派发的唯一接力凭证，必须原样保存为 RELAY_TURN 变量，不得自行生成。后续所有请求必须把它作为顶层 relay_turn 字段提交，严禁放入 payload。ACCEPTED 和 DECLINED 都一样：HTTP 成功只表示 S4 完成，必须立即从 S5 继续；不得输出「等待引擎」后停止。",
                "S5/8 更新GAP：不再重新读取图谱；在本地把 S1 获取的 TaskContext.all_done_output 与当前节点实际产出和验收事实合并，重新计算仍未覆盖的根验收范围，更新本地 TaskContext.gaps。本阶段不调用 HTTP 上报接口。",
                "S6/8 解析下一棒 TaskNode：若仍有 GAP，基于更新后的 gaps 在本地构造唯一 next_task_spec(context+goal)；节点 ID 由 Graph 生成，Skill 不自行生成。若无 GAP，则 next_task_spec=null 并标记任务可收口。本阶段不调用 HTTP 上报接口。",
                f'S7/8 搜推并指定执行者：先用统一上报接口把 S5/S6 的规划事实写入图谱，POST {callback}，event_type=PLAN_RESULT；请求顶层字段必须包含 task_id、node_id、event_type、event_id、holder_id、relay_turn、progress_reason，payload={{gaps,next_task_spec}}。gaps 非空时 next_task_spec 必须是唯一下一棒 context+goal；PLAN_RESULT HTTP 成功且 gaps 非空时必须原样保存响应中的 target_node_id，未成功读取该值只能用相同 event_id 原样重试。随后 POST {backend}/api/v1/collaboration/tasks/search，请求体只能传 {{"query": "..."}}；再由 Skill 根据真实候选判断 HIT_SINGLE、HIT_MULTI_BOTS 或 MISS，并向 callback/report 上报 event_type=DISPATCH_RESULT。node_id 必须是 PLAN_RESULT 返回的 target_node_id；payload 使用 outcome、run_mode、driver_bot_id、next_relay_bots；当前棒不得成为下一棒，协作群 driver 必须属于 next_relay_bots，Human 默认作为 observer。MISS 必须提供 miss_reason。在最终 PLAN_RESULT(gaps=[], next_task_spec=null) HTTP 200 前，不得向用户宣称任务已完成。',
                f"S8/8 实际交接：HIT 后 POST {backend}/api/v1/collaboration/tasks/dispatch，传 task_id、origin_node_id、target_node_id、holder_id、relay_turn、唯一 dispatch_id。必须先满足 target_node_id 来自 PLAN_RESULT 原始响应且不等于 origin_node_id、同一 target_node_id 的 DISPATCH_RESULT 已 HTTP 200、relay_turn 未过期且属于当前 origin。缺少任一前置变量时停止等待恢复，不得用 root/task_id 猜测 target，也不得直接调用 /dispatch。只有 HTTP 200 才算交接成功；/dispatch 后由 TaskRunner 按 assignee/run_mode 创建单 Bot 会话或协作群，并把提交任务 Human 以 observer 拉入会话。MISS 自动发布 BBS；无 GAP 时标记任务收口、无下一棒。",
                "BBS 认领者执行完成后也从 S1 开始，继续同一接力闭环。只允许使用本指令明确列出的 context、callback/report、search、dispatch、BBS claim 五个端点；404 时必须校验路径和 task_id/node_id，不得换近似 URL 继续探测。任一接口失败时不得伪造成功；在 failure_reason 记录真实原因。",
                "用户可见文案本地化：DECLINED、capability_mismatch 等内部枚举/失败码只用于 API 请求和排障，不得原样回复给用户。若能力不匹配，面向用户只说明「当前 Bot 能力不匹配，未执行本节点业务子项，将转交更合适的 Bot 接续执行」；若未找到候选，则说明「未找到能力匹配的 Bot，任务已发布到广场等待认领」。",
                OUTPUT_LANGUAGE_CONSTRAINT,
            ]
        )

    payload = {
        "task_id": task_id,
        "node_id": node_id,
        "status": "SUCCESS",
        "output": "driver 汇总后的完整节点最终输出",
        "acceptance_result": {
            "verdict": "DONE",
            "acceptances_metric": [
                {"id": "验收项ID", "passed": True, "summary": "可核验的证据摘要"}
            ],
            "gaps": [],
        },
        "extend_props": {},
    }
    parts = [
        "[task-execute]",
        "【业务节点执行协议】本协议只约束业务执行、验收和回投；成员派发、消息收集与群收尾由系统上下文处理，不要自行调用或复述这些调度动作。",
        context,
        f"唯一回投者: {reporter}。除唯一回投者外，任何成员都不得调用节点 callback。",
        f"本群执行者: {json.dumps(executors, ensure_ascii=False)}。driver/manager 同时是执行者，必须完成自己的业务推理，不得只派发后等待成员。",
        f"目标:{objective}",
        f"指令:{instruction}",
        f"验收标准:{json.dumps(acceptances, ensure_ascii=False)}",
        f"上游产出:{json.dumps(upstream_outputs or {}, ensure_ascii=False, default=str)}",
        "执行顺序不可跳过：1) 每位执行者（包括 driver）完成分内真实业务推理并给出可复核产出；2) driver 在收到所有成员结果后汇总，不能照抄成员原文或替未回复成员编造结果；3) driver 用汇总结果逐条核验全部验收项；4) driver 形成一次且仅一次的节点最终 output 与验收结论；5) driver 回投并确认成功。",
        "验收项覆盖要求：acceptances_metric 必须逐条且仅一次覆盖上面的每个验收项 id；每项包含 id、passed（布尔值）和 summary（证据摘要）。全部通过：status=SUCCESS、verdict=DONE、gaps=[]；存在未满足项：status=DONE、verdict=FAILED、gaps 必须逐条说明；只有实际执行异常才可使用 status=FAILED。",
        "执行约束：禁止联网检索、浏览外部网页或访问外部 API 获取信息；仅依据给定上下文与自身知识完成业务。下方指定的唯一节点回投接口不受此限制，必须按规定调用。",
    ]
    if not skill_report_enabled:
        parts.append(_no_callback_instruction())
        return "\n".join(parts)
    parts.extend(
        [
            "【唯一允许的节点回投】仅唯一回投者可使用 exec/curl，以 POST JSON 请求固定地址；不得使用 web_search、web_fetch、浏览器、群调度工具或任何猜测的 URL/字段进行回投。",
            f"POST {callback}",
            "请求体必须且只能包含以下六个顶层字段；loop_task_id 已由 task_id/node_id 在服务端还原，严禁放入请求体：",
            json.dumps(payload, ensure_ascii=False),
            "回投前必须将示例中的 task_id/node_id 替换为当前节点值，将 output 替换为完整汇总结果，并填入完整验收结果。HTTP 200 且响应明确表示成功，才算回投成功。",
            "失败处理：仅可对同一固定 URL、同一字段结构做一次原样重试；不得改 URL、换工具、猜字段或重复生成 output。收到 HTTP 200 后立即停止，不得再次 POST，不得重贴完整输出；若需回复，只能一句话确认已上报。",
            OUTPUT_LANGUAGE_CONSTRAINT,
        ]
    )
    return "\n".join(parts)


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
    return chr(10).join(
        [
            "【执行闭环·三步缺一不可,顺序为承接→执行→交接】",
            "严格按上方【接力执行】顺序逐步完成,不得跳过、合并或只用一句话概括代替:",
            "- 接力上下文:承接时复述接自谁、上游规划交你执行的下一步任务是什么、你的承接边界;",
            "- 执行产出:真正推理完成本节点任务指令,给出完整执行结论与产出,不得只复述任务不产出;",
            "- gap与交接:获取上方最新统一上下文(上游产出正文+本步产出),自己对照目标计算 gap、规划下一步任务,并在正文中写明下一步交给谁、做什么;本链路终点节点则在此声明接力终结、不再派发。",
            "【协作群多轮接力(仅当上方【## 群组成】列出除你之外的其它 Bot 成员)】三步按多轮展开,承接→中间执行→收齐汇总三轮缺一不可,不得合并成单轮;你是 driver(协调者)时按 1-3 逐轮执行,你是被派发成员(consultant 等)时只做第2步各方执行:",
            "1) 承接轮(driver 先单独回复):复述接自谁、上游交你的下一步任务、你的承接边界,并说明本群各成员 Bot 分工(含 driver 自己的职责);此轮只发承接,不派发、不自评、不产出任何执行结论,更不得给出 gap 与交接。",
            "2) 派发 + 各方执行轮(driver):对【## 群组成】中每个非人类 Bot 成员用 bcs_assign_task(主从群)或 bcs_route(chat 群)派发;派发消息必须把上方【## 上游产出正文】全文复制进去,并在消息内声明“此即本次完整输入,不得请求更多详情”;human 仅为观察者,不得向 human 派发;driver 自己也是执行者,须并行完成自己职责范围内的产出,不得把全部执行甩给成员。",
            "被派发成员:上方【## 上游产出正文】及派发消息带入的正文即为完整输入,视为已提供全部所需上下文;无论详略,立即基于此 + 自身合理假设给出你职责内的完整审查结论或修订条件;严禁以“缺详情/需完整方案”为由请求追加输入、要求同步方案、暂停或拒绝执行——一律直接产出结论。",
            "本轮各方给出的(含 driver 自评)都只是成员级产出,不是本协作群最终执行产出;本轮不得做 gap 计算、不得给出交接;driver 派发完毕即结束本轮,等待成员回复,不得在同一轮强行追加汇总或交接。",
            "3) 收齐汇总与交接轮(driver):只有当所有成员(含 driver 作 worker)都已在本群回复各自产出后,driver 才进入此轮;综合全部成员产出与自己已完成产出 = 本协作群执行产出(即“执行产出”步),并对照目标计算 gap、规划下一步任务(即“gap与交接”步),在正文描述下一步交给谁、做什么,不路由群外;此轮是本协作群的最终输出——执行产出与 gap交接只在此轮给出,承接/派发/自评/成员回复等中间轮一律不得提前给出执行产出与 gap交接。",
            "判定收齐:群内每个非人类成员均已产出其职责结论后即视为收齐,driver 立即进入汇总轮;若仍有成员未回复,继续等待,不得提前收尾或替未回成员编造意见。",
            "此轮只综合成员回复成新结论,不重贴承接段与成员原文。",
            "【单人接力(无【## 群组成】或仅你一人)】上述三步一次性在正文中给出完整产出,不得只复述任务不产出。",
            "【交接只描述,不路由群外】交接部分的下一步派发仅在正文描述;真正的节点间派发由平台/workflow 引擎完成;禁止用 bcs_route 或 bcs_assign_task 路由给【## 群组成】之外的 bot(群外下一节点);也不要调用 bcs_task_complete / bcs_fuse 等收尾工具(本节点结果由平台统一回收)。",
            "【正文不重复输出】不要把你已给出的承接/产出在轮末二次重述、或作为 summary 收尾再发一遍;协作群汇总阶段只综合成员回复成新结论,不重贴你的承接段。",
            "输出要求:不要用 step 编号或第N步式序号作小标题机械罗列三步;正文须保留 markdown 排版——用标题、段落、列表、表格、加粗等结构把承接、产出与交接组织清晰、分区呈现,保持专业层级与可读性,读起来是结构化的专业产出,而非一段扁平纯文本或分步打卡清单。",
            "执行约束:禁止调用联网搜索/web_search/联网检索工具或浏览外部网页、外部 API 等外部网络资源获取信息,仅依据上方给定上下文与自身知识产出结论。",
            "本节点执行结果由平台统一回收,无需你主动调用上报接口或构造节点级 Push;你在群内给出完整执行产出即可,产出内容只呈现本步执行结论与下一步交接。",
        ]
    )


class PromptFormatterImpl(PromptFormatter):
    def format_execute(self, context: dict[str, Any], node: TaskNode) -> str:
        instr = (
            context.get("node_instruction")
            or node.run_info.extend_props.get("execution_prompt")
            or task_spec_instruction(node.task_spec)
        )
        # 接力交接节点:instruction 由 StaticPlanRuntime._decorate 注入 "# 接自 ..." 三段
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
        return format_task_node_business_instruction(
            task_id=str(context.get("task_id") or node.task_id),
            node_id=str(context.get("node_id") or node.node_id),
            backend=str(context.get("backend") or "{backend}"),
            objective=goal,
            instruction=str(instr),
            acceptances=acceptances,
            upstream_outputs=siblings,
            reporter_bot_id=str(
                context.get("reporter_bot_id") or node.run_info.assignee or ""
            ),
            executor_bot_ids=[str(node.run_info.assignee)]
            if node.run_info.assignee
            else None,
            skill_report_enabled=bool(context.get("skill_report_enabled", True)),
            relay_execution=bool(
                node.node_run_graph
                and (
                    node.node_run_graph.extend_props.get("execution_config", {}) or {}
                ).get("orchestration_mode")
                == "relay"
            ),
            relay_blackboard=context.get("relay_blackboard"),
        )

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


def format_benchmark_prompt(task_spec: dict[str, Any]) -> str:
    """Construct a prompt message from task_spec JSON for benchmark_execute.

    Takes the raw task_spec dict (as received in TaskInfoRequestDTO.task_spec)
    and builds a structured prompt to send directly to the bot via
    OpenApiBotPort.send_message — bypassing the normal engine dispatch flow.

    Fields (all optional, missing fields are simply skipped):
    - metadata.title / metadata.instruction
    - context.background / context.extend_props
    - goal.objective / goal.acceptances
    """
    metadata = task_spec.get("metadata") or {}
    context = task_spec.get("context") or {}
    goal = task_spec.get("goal") or {}
    extend_props = context.get("extend_props") or {}

    parts: list[str] = []

    # Title
    title = metadata.get("title") or ""
    if title:
        parts.append(f"# {title}")

    # Instruction (the core prompt for the bot)
    instruction = metadata.get("instruction") or ""
    if instruction:
        parts.append(f"## 执行指令\n{instruction}")

    # Background
    background = context.get("background") or ""
    if background:
        parts.append(f"## 任务背景\n{background}")

    # Goal / objective
    objective = goal.get("objective") or ""
    if objective:
        parts.append(f"## 任务目标\n{objective}")

    # Acceptance criteria
    acceptances = goal.get("acceptances") or []
    if acceptances:
        acc_lines = []
        for acc in acceptances:
            acc_id = acc.get("id") or ""
            # Request DTO uses "acceptance", response DTO uses "description"
            acc_desc = acc.get("acceptance") or acc.get("description") or ""
            acc_lines.append(f"- [{acc_id}] {acc_desc}")
        parts.append("## 验收标准\n" + "\n".join(acc_lines))

    # Key abilities from extend_props
    key_abilities = extend_props.get("key_abilities") or []
    if key_abilities:
        parts.append("## 关键能力要求\n" + "\n".join(f"- {a}" for a in key_abilities))

    # Chain order (handoff sequence) from extend_props
    chain_order = extend_props.get("chain_order") or []
    if chain_order:
        chain_lines = []
        for step in chain_order:
            step_num = step.get("step") or ""
            name = step.get("name") or ""
            rationale = step.get("rationale") or ""
            modality_type = step.get("modality_type") or ""
            chain_lines.append(
                f"### 步骤 {step_num}: {name}"
                + (f" (类型: {modality_type})" if modality_type else "")
                + (f"\n{rationale}" if rationale else "")
            )
        parts.append("## 交接链路\n" + "\n".join(chain_lines))

    # Benchmark task ID
    benchmark_task_id = extend_props.get("benchmark_task_id") or ""
    if benchmark_task_id:
        parts.append(f"**Benchmark 任务 ID**: {benchmark_task_id}")

    # Business type
    business_type = extend_props.get("business_type") or ""
    if business_type:
        parts.append(f"**任务类型**: {business_type}")

    # Constraints
    parts.append(NO_WEB_SEARCH_CONSTRAINT)
    parts.append(OUTPUT_LANGUAGE_CONSTRAINT)

    return "/task " + "\n\n".join(parts)
