"""Session-detail probing helpers for ``TaskTrajectoryService``."""

from __future__ import annotations

import json
import logging
import re
import time

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TaskTrajectory,
)

logger = logging.getLogger("task.trajectory")
_RUNNING_SESSION_DEFAULT_MSG_LIMIT = 200
_ENGINE_SESSION_KEY_RE = re.compile(
    r"^(?:(?:agent:[^:]+:)?session:[^:]+:user:[^:]+"
    r"|user:[^:]+:session:[^:]+:agent:[^:]+)$",
    re.IGNORECASE,
)


def _is_engine_session_id(session_id: str) -> bool:
    return bool(session_id) and _ENGINE_SESSION_KEY_RE.match(session_id) is not None


class TrajectorySessionSupport:
    def _last_event_ext_by_node(self, records) -> "dict[str, dict]":
        """每个 (node_id) 的**最后一条**事件行的 ext_info(解析后 dict;无/损坏 → 空)。

        records 已按 (gmt_create, id) 正序——倒序扫,首见即末位行;解析失败/非 dict
        视作无 ext_info(缓存判断退化为"未缓存",重新实际查询,不抛)。"""
        last: dict[str, dict] = {}
        for rec in reversed(records or []):
            nid = getattr(rec, "node_id", None)
            if nid in last:
                continue
            raw = getattr(rec, "ext_info", None)
            if not raw:
                last[nid] = {}
                continue
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                last[nid] = {}
                continue
            last[nid] = parsed if isinstance(parsed, dict) else {}
        return last

    async def _collect_session_briefs(
        self, task_id: str, records, *, force_analysis: bool = False,
    ) -> "tuple[list[dict] | None, dict[str, list]]":
        """物化每个子任务的会话明细 + 汇集**未结束**节点的大模型研判原料。

        两段式(需求 2026-09-22):

        ① **物化(全节点,不管有没有完成)**:对每个带 ``session_id`` 的
           (task_id,node_id) 子任务,取最近 ``limit``(默认 200,内容**不截断**)
           条 session msgs:末位事件 ``ext_info`` 已带 ``session_msgs`` →
           **缓存命中,不再实际拉取**;否则按 session_id 形态分派读通道实拉
           (BCS 会话键 → BCS;引擎 key ``agent:main:session:*`` 等 → BaaS
           Open API,见 ``_fetch_node_session_msgs``)→
           ``repo.merge_last_event_ext_info`` 把 ``session_msgs`` **增量合并**
           进该子任务最后一条事件的 ext_info(只添/更新该键,不覆盖既有其它键)。

           ``force_analysis=True``(强制重跑分析):**缓存旁路**——即使已带
           session_msgs 也重新实际拉取,并 merge 覆盖该键为最新内容
           (与 force 语义一致:一切以当下现场为准)。

        ② **研判原料(仅未结束节点)``running_sessions``**:状态非终态
           (非 SUCCESS/FAILED/CANCELLED——含 RUNNING/DONE/HUNG 等未收口态)
           且有 msgs 的节点,组成 brief(node_id/node_status/session_id/
           elapsed_ms/message_count/messages——原文,不截断)交给分析大模型:
           结合会话内容判断是否异常——工具调用报错、执行已结束但一直不主动
           上报结果、长时间无新进展等时间线看不到的问题。

        Guarantees: NEVER raises(逐步降级 + WARNING);graph 未接线或两条读
        通道(BCS / OpenApiBot)均未接线 → ``(None, {})``。
        """
        logger.info("[task][trajectory], collect_trajectory_event_session_msgs, begin")

        # 探测门:graph 必须接线;BCS 与 BaaS-OpenAPI 两通道可只接其一
        # (所缺通道的节点在 _fetch_node_session_msgs 内逐个 WARNING 降级)。
        if self._graph is None or (self._bcs is None and self._openapi_bot is None):
            logger.info("[task][trajectory], collect_trajectory_event_session_msgs, graph or no read channel wired")
            return None, {}
        nodes = self._session_bearing_nodes(task_id)
        if not nodes:
            logger.info("[task][trajectory], collect_trajectory_event_session_msgs, no session nodes")
            return None, {}
        now_ms = int(time.time() * 1000)

        last_ext = self._last_event_ext_by_node(records)
        limit = self._resolve_msg_limit()

        _UNFINISHED = {Status.RUNNING, Status.PENDING, Status.PLANNING,
                       Status.DONE, Status.HUNG}
        briefs: list[dict] = []
        fresh_msgs: dict[str, list] = {}  # 本次实际拉取的 msgs(node_id → 原文摘录),供返回态 session_msgs 富化
        for node in nodes:
            ep = node.run_info.extend_props or {}
            sid = ep.get("session_id")
            if not sid or not isinstance(sid, str):
                continue
            cached = (last_ext.get(node.node_id) or {}).get("session_msgs")
            state = getattr(node.status, "value", node.status)
            if isinstance(cached, list) and cached and not force_analysis:
                excerpt = cached           # ①缓存命中:末位事件 ext_info 已有,不再实际查
                logger.info(
                    "[task][trajectory], collect_trajectory_event_session_msgs,"
                    " session_msgs 缓存命中 task=%s node=%s msgs=%d",
                    task_id, node.node_id, len(cached),
                )
            else:
                # 通道分派实拉(BCS / BaaS-OpenAPI);失败已在 helper 内 WARNING
                # 降级。空列表(拉到但无消息)与旧 `if not msgs: continue` 同义。
                excerpt = await self._fetch_node_session_msgs(
                    task_id, node, limit=limit,
                )
                if not excerpt:
                    continue
                # ①物化:增量合并进该子任务最后一条事件的 ext_info(不覆盖既有键)。
                fresh_msgs[node.node_id] = excerpt  # 本次实拉内容 → 返回态可用
                try:
                    merged_ok = self._repo.merge_last_event_ext_info(
                        task_id, node.node_id, "session_msgs", excerpt,
                    )
                    if not merged_ok:
                        logger.info(
                            "[task][trajectory] session_msgs 未能合并(该子任务无事件行或 ext_info 不可解析)"
                            " task=%s node=%s", task_id, node.node_id,
                        )
                except Exception as ex:  # noqa: BLE001  物化失败不影响研判
                    logger.warning(
                        "[task][trajectory] session_msgs 合并失败(不影响主分析) task=%s node=%s: %s %s",
                        task_id, node.node_id, type(ex).__name__, ex,
                    )

            # ②研判原料:仅未结束节点喂模型(msgs 原文)
            if state not in {s.value for s in _UNFINISHED}:
                continue
            # NB:此处 excerpt 必非空——缓存命中分支以非空为前提,fetched 分支的
            # 空 excerpt 已在其紧邻的 ``if not excerpt: continue`` 截走(原双重
            # 防御中的后一处恒不可达,已删,保持 100% 覆盖诚实)。
            briefs.append({
                "node_id": node.node_id,
                "node_status": str(state),
                "session_id": sid,
                "elapsed_ms": self._node_elapsed_ms(node, now_ms),
                "message_count": len(excerpt),
                "messages": list(excerpt),
            })
        if not briefs:
            return None, fresh_msgs
        logger.info(
            "[task][trajectory] collect_trajectory_event_session_msgs, RUNNING 会话探测完成 task=%s briefs=%d",
            task_id, len(briefs),
        )
        return briefs, fresh_msgs

    # ------------------------------------------------------------------
    # 会话明细探测共享件:节点收集/排序、limit 解析、单节点按 sid 分派实拉。
    # 两条编排路径共用——分析路径(cache-first→物化落库→briefs)与纯读路径
    # (always-live→不落库→失败回退缓存快照);策略差异留在各编排层,机械件下沉。
    # ------------------------------------------------------------------

    def _resolve_msg_limit(self) -> int:
        """每 session 最大消息条数(TrajectoryAnalysisConfig.
        running_session_message_limit,默认 200;解析失败/未配置回落默认)。"""
        try:
            return int(
                getattr(self._config, "running_session_message_limit",
                        _RUNNING_SESSION_DEFAULT_MSG_LIMIT)
                or _RUNNING_SESSION_DEFAULT_MSG_LIMIT
            )
        except (TypeError, ValueError):
            return _RUNNING_SESSION_DEFAULT_MSG_LIMIT

    @staticmethod
    def _node_elapsed_ms(node, now_ms: int) -> int:  # noqa: ANN001  domain node;防御性读
        """节点已运行毫秒数(run_info.start_time 防御性读;缺/坏 → 0)。"""
        try:
            st = getattr(node.run_info, "start_time", None)
            return now_ms - int(st) if st is not None else 0
        except (TypeError, ValueError):
            return 0

    def _session_bearing_nodes(self, task_id: str) -> "list | None":
        """带 ``session_id`` 的节点列表,按卡住时长降序(预算/研判优先最"病"的)。

        graph 未接线(轻量 DI)→ ``None``(INFO,探测关闭);图读失败(任务已删 /
        relay 外部托管等)→ ``None``(WARNING,探测整体降级);无会话节点 → ``[]``。
        NEVER raises。"""
        if self._graph is None:
            logger.info("[task][trajectory] session 探测未接线(graph is none) task=%s", task_id)
            return None
        try:
            graph = self._graph.query_task_dashboard(task_id)
        except Exception as ex:  # noqa: BLE001  图读失败 → 探测整体降级
            logger.warning(
                "[task][trajectory] session 探测图读失败 task=%s: %s: %s",
                task_id, type(ex).__name__, ex,
            )
            return None
        nodes = [n for n in getattr(graph, "tasks", []) if
                 (n.run_info.extend_props or {}).get("session_id")]
        now_ms = int(time.time() * 1000)
        nodes.sort(key=lambda n: self._node_elapsed_ms(n, now_ms), reverse=True)
        return nodes

    async def _fetch_node_session_msgs(
        self, task_id: str, node, *, limit: int,
    ) -> "list[dict] | None":
        """单节点会话明细实拉,按 session_id 形态分派读通道:

        * **引擎会话**(``_is_engine_session_id``:``agent:main:session:*`` 等)
          → ``OpenApiBotPort.get_session_messages``(BaaS Open API;同派发链的
          api_key,**鉴权面=派发面**——凡任务派发成功的 bot,同 key 读回必过
          policy gate)。``bot_id`` 复合地址与执行侧同源
          (``compose_bot_identity(assignee, assignee_owner_id)``,
          task_executor.py 同式);根节点 main 会话无 assignee → ``None``
          → BaaS 按 api_key 默认解析,4xx 逐节点降级。
        * **BCS 会话键**(``bcs-cli:*`` / 协作群)→ 既有 ``get_session_messages``
          (参与者级 ACL:带持有者 bot 的 Bearer,见 ``_holder_bearer_token``)。

        返回 ``[{role, content}]`` excerpt(内容不截断);``[]``=拉到但无消息;
        ``None``=失败/通道未接线(已 WARNING)。NEVER raises;**永不落库**
        (物化 merge 由分析编排层决定,纯读路径不触 DB 写)。
        """
        ep = node.run_info.extend_props or {}
        sid = ep.get("session_id")
        if not sid or not isinstance(sid, str):
            return None
        try:
            if _is_engine_session_id(sid):
                if self._openapi_bot is None:
                    logger.warning(
                        "[task][trajectory] 引擎会话但 OpenApiBotPort 未接线,跳过"
                        " task=%s node=%s session=%s",
                        task_id, getattr(node, "node_id", None), sid,
                    )
                    return None
                # 与执行侧同源身份(单 bot 派发 compose_bot_identity(assignee,
                # assignee_owner_id));根节点 main 会话无 assignee → None 默认解析。
                assignee = getattr(node.run_info, "assignee", None)
                bot_id = compose_bot_identity(assignee, ep.get("assignee_owner_id")) \
                    if assignee else None
                lifecycle_stage = str(ep.get("lifecycle_stage") or "").strip() or None
                logger.info(
                    "[task][trajectory] session_msgs 引擎通道拉取 begin task=%s node=%s"
                    " holder_bot=%s lifecycle_stage=%s", task_id,
                    getattr(node, "node_id", None),
                    bot_id or "<api-key-default>",
                    lifecycle_stage or "<default>",
                )
                msgs = await self._openapi_bot.get_session_messages(
                    sid, limit=limit, bot_id=bot_id,
                    lifecycle_stage=lifecycle_stage,
                )
                logger.info("[task][trajectory] session_msgs 引擎通道拉取 finish")
            else:
                if self._bcs is None:
                    logger.warning(
                        "[task][trajectory] BCS 会话但 BcsClientPort 未接线,跳过"
                        " task=%s node=%s session=%s",
                        task_id, getattr(node, "node_id", None), sid,
                    )
                    return None
                # BCS 会话历史读口是参与者级 ACL(401 "valid Human identity or
                # Bot token is required"):带持有者 bot 的 session_token 做
                # ``Authorization: Bearer``(执行 Bot 取值序 driver_bot_id →
                # assignee,详见 _holder_bearer_token)。
                caller_bearer = self._holder_bearer_token(node)
                logger.info(
                    "[task][trajectory], collect_trajectory_event_session_msgs,"
                    " begin get bcs msgs holder_bearer=%s",
                    "yes" if caller_bearer else "hmac-only",
                )
                msgs = await self._bcs.get_session_messages(
                    sid, limit=limit, caller_bearer=caller_bearer,
                )
                logger.info("[task][trajectory], collect_trajectory_event_session_msgs, finish get bcs msgs")
            excerpt = []
            for m in msgs or []:
                if not isinstance(m, dict):
                    continue  # 非预期形态 → 跳过该条(防御)
                excerpt.append({
                    "role": str(m.get("role") or ""),
                    "content": str(m.get("content") or ""),
                })  # 内容不截断(已去掉单条/总量字符预算,原文进库/展示)
            return excerpt
        except Exception as ex:  # noqa: BLE001  单节点拉取失败 → 跳过,不拖垮其余
            logger.warning(
                "[task][trajectory] 会话明细拉取失败,跳过 task=%s node=%s session=%s: %s: %s",
                task_id, getattr(node, "node_id", None), sid, type(ex).__name__, ex,
            )
            return None

    async def _live_session_msgs_for_read(self, task_id: str) -> "dict[str, list]":
        """纯读路径的会话明细实拉(REQ-8 不变式:外读内不写,零 DB 写)。

        遍历带 session_id 的节点逐个实拉(既有场景成本:观测页人工访问,
        每读 1+N 次外部 GET;降级保证永不 raise)。成功且非空的摘录进
        (node_id → excerpt) map——失败/空(不遮已可见的缓存内容)均缺席,
        由 ``_attach_session_msgs`` 的 fresh>缓存 覆盖语义自然回退快照。"""
        nodes = self._session_bearing_nodes(task_id)
        if not nodes:
            logger.info(
                "[task][trajectory], live_session_msgs_for_read, probe off or no session nodes task=%s",
                task_id,
            )
            return {}
        limit = self._resolve_msg_limit()
        fresh: dict[str, list] = {}
        for node in nodes:
            excerpt = await self._fetch_node_session_msgs(task_id, node, limit=limit)
            if excerpt:
                fresh[node.node_id] = excerpt
        logger.info(
            "[task][trajectory], live_session_msgs_for_read, 纯读实拉完成 task=%s nodes=%d fetched=%d",
            task_id, len(nodes), len(fresh),
        )
        return fresh

    def _safe_list_records(self, task_id: str) -> "list | None":
        """读事件行(读路径富化用);失败 → WARNING + None(富化降级,不影响轨迹本体)。"""
        try:
            return self._repo.list_events_by_task(task_id)
        except Exception as ex:  # noqa: BLE001  富化读失败 → 返回未富化轨迹
            logger.warning(
                "[task][trajectory] session_msgs 富化读事件行失败,跳过 task=%s: %s: %s",
                task_id, type(ex).__name__, ex,
            )
            return None

    def _collect_session_msgs_by_node(
        self, records, fresh: "dict[str, list] | None" = None,
    ) -> "dict[str, list]":
        """按子任务归集会话明细(2026-10-16 起**不再逐事件挂载**:
        返回 ``{node_id: msgs}``,由调用方经 ``_append_result_display_events``
        挂到 ``task_result_display`` 虚拟展示事件)。

        来源优先级:``fresh``(本回合 do_analysis 刚实拉的最新内容) > 事件行快照
        中该子任务末位事件 ``ext_info.session_msgs``(早前物化的缓存)。观测旁路:
        任何失败返回已归集部分(至少空 dict),绝不抛。
        """
        msgs_by_node: dict[str, list] = {}
        try:
            for node_id, ext in self._last_event_ext_by_node(records).items():
                cached = (ext or {}).get("session_msgs")
                if isinstance(cached, list) and cached:
                    msgs_by_node[node_id] = cached
        except Exception:  # noqa: BLE001  records 为 None(读失败)→ 空映射
            msgs_by_node = {}
        if fresh:
            msgs_by_node.update(
                {k: v for k, v in fresh.items() if isinstance(v, list) and v}
            )
        return msgs_by_node

    def _holder_bearer_token(self, node) -> "str | None":
        """解析 RUNNING 节点**持有者 bot** 的 BCS session_token(会话历史读取身份)。

        执行 Bot 取值序(都是执行链路落在 extend_props/assignee 上的事实字段):
        ``driver_bot_id``(协作群 driver)→ ``run_info.assignee``(single_bot
        执行 bot;协作群时该值是
        group_id——交给 provider 解析,自然 None)。经 ``BcsBotTokenProvider``
        (corp 读 ``bcs_bots.session_token``,带缓存)取 token;未注入 provider /
        id 缺失 / 解析失败 → ``None``,调用方回退裸 HMAC(BCS 按 401 拒,走
        既有 WARNING 降级)。全程防御,永不抛。token 不落日志。
        """
        if self._bcs_bot_tokens is None:
            return None
        try:
            ep = node.run_info.extend_props or {}
            holder = ep.get("driver_bot_id") or getattr(node.run_info, "assignee", None)
            logger.info("[task][trajectory], collect_trajectory_event_session_msgs, hold is = %s", holder)
            if not holder or not isinstance(holder, str):
                return None
            return self._bcs_bot_tokens.get_token(holder) or None
        except Exception:  # noqa: BLE001  身份解析是观测旁路,任何失败回退匿名
            return None

    # ------------------------------------------------------------------
    # Config resolution — optional DI; None when unbound (lightweight injectors)
    # ------------------------------------------------------------------

    def _resolve_analysis_bot_id(self) -> "str | None":
        """Read the deployment-configured, env-aware ``analysis_bot_id`` (decision #10).

        Mirrors ``openapi_bot.base_url`` env selection (``_env_select(prod,
        pre)``): pre env → ``analysis_bot_id_pre``, else → ``analysis_bot_id``.
        The pair is deployment-configured; ``get_current_env()`` normalizes
        prepub→pre / gray→prod (lazy import to avoid a module-load cycle, same
        idiom as ``_env_select``). A ``None`` config (lightweight DI that did not
        bind ``TrajectoryAnalysisConfig``) — or the env-relevant variant being
        unset — is treated as not-configured: the ``do_analysis=true`` call
        raises ``TrajectoryAnalysisNotConfiguredError`` (503). The default read
        path (``do_analysis=false``) never calls this.
        """
        if self._config is None:
            return None
        from agentclaw.community.utils.env_utils import get_current_env
        if get_current_env() == "pre":
            return self._config.analysis_bot_id_pre
        return self._config.analysis_bot_id

    # ------------------------------------------------------------------
