"""Validated, repository-owned static-plan task plan definitions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskNode,
    TaskSpec,
    task_spec_instruction,
)


logger = logging.getLogger("task.static_plan")


_PLACEHOLDER_RE = re.compile(r"\${(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}")


def _expand_placeholders(data: Any, bindings: Mapping[str, str]) -> Any:
    """Resolve ``${role_key}`` / ``${role_key:-default}`` from an injected
    bindings map (role_key -> bot uuid).

    Only reached when a NON-EMPTY ``bindings`` map is passed (env-injected
    deployment); ``None`` (test seam) and an empty map (env unset) are both
    falsy and skip expansion, leaving ``${...}`` literal so content
    routing/materialize stays green on a bare/CI build and only a real
    dispatch hits BCS with a literal (where it rejects it). With a non-empty map,
    a referenced role_key that has neither a binding nor a default is a config
    error (raised at load) so a half-wired real deployment fails loudly.
    """

    def _repl(match: re.Match[str]) -> str:
        name = match.group("name")
        if name in bindings:
            return str(bindings[name])
        default = match.group(2)
        if default is not None:
            return default
        raise KeyError(
            f"task static-plan placeholder ${{{name}}} has no binding and no default"
        )

    if isinstance(data, dict):
        return {k: _expand_placeholders(v, bindings) for k, v in data.items()}
    if isinstance(data, list):
        return [_expand_placeholders(v, bindings) for v in data]
    if isinstance(data, str):
        return _PLACEHOLDER_RE.sub(_repl, data)
    return data


@dataclass(frozen=True)
class StaticPlanNodeDefinition:
    node_id: str
    name: str
    node_type: str
    depends_on: tuple[str, ...]
    bot_id: str | None
    bot_ids: tuple[str | None, ...]
    input: dict[str, Any]
    output: dict[str, Any]
    enabled_when: str | None = None
    # V2 接力输入: 框架下发给 bot 的"# 接自 / ## 群组成 / ## 上游产出正文 / ## 本角色任务"里,
    # "本角色任务"文本由这里给出(rule 不归框架,在各 bot 的 skill/rule 配置)。
    task: str = ""
    # 节点在 dashboard 显示的短标题(goal/objective 标签);缺省回退 task/name。
    title: str = ""
    # collaboration 节点的群组成角色名,与 bot_ids 平行;bot_names[0] = driver/总结者。
    bot_names: tuple[str, ...] = ()

    @property
    def all_bot_ids(self) -> tuple[str | None, ...]:
        return ((self.bot_id,) if self.bot_id is not None else ()) + self.bot_ids


@dataclass(frozen=True)
class StaticPlanDefinition:
    template_id: str
    entry_bot_id: str | None
    input_schema: dict[str, Any]
    nodes: tuple[StaticPlanNodeDefinition, ...]
    # V2 接力: 入口接自身份展示名,与 entry_bot_id 一起渲染 "# 接自: <entry_name>(<entry_bot_id>)"。
    entry_name: str | None = None

    @classmethod
    def from_yaml(
        cls, text: str, *, bindings: Mapping[str, str] | None = None
    ) -> "StaticPlanDefinition":
        raw = yaml.safe_load(text) or {}
        if bindings:
            raw = _expand_placeholders(raw, bindings)
        if not isinstance(raw, dict) or not raw.get("template_id"):
            raise ValueError("static plan requires template_id")
        nodes: list[StaticPlanNodeDefinition] = []
        ids: set[str] = set()
        for item in raw.get("nodes", []):
            if not isinstance(item, dict) or not item.get("id"):
                raise ValueError("static plan node requires id")
            node_id = str(item["id"])
            if node_id in ids:
                raise ValueError(f"duplicate static plan node: {node_id}")
            ids.add(node_id)
            collab = item.get("collaboration") or {}
            nodes.append(
                StaticPlanNodeDefinition(
                    node_id=node_id,
                    name=str(item.get("name", node_id)),
                    node_type=str(item.get("type", "bot")),
                    depends_on=tuple(str(x) for x in item.get("depends_on", [])),
                    bot_id=item.get("bot_id"),
                    bot_ids=tuple(collab.get("bot_ids", [])),
                    input=dict(item.get("input") or {}),
                    output=dict(item.get("output") or {}),
                    enabled_when=(item.get("enabled_when") or {}).get("expression")
                    if isinstance(item.get("enabled_when"), dict)
                    else None,
                    # V2 接力输入(本角色任务 + 群组成角色名);缺省为空(老模板走旧行为,不变)。
                    task=str(item.get("task") or "").strip(),
                    title=str(item.get("title") or "").strip(),
                    bot_names=tuple(str(x) for x in collab.get("bot_names", [])),
                )
            )
        known = {n.node_id for n in nodes}
        for node in nodes:
            missing = set(node.depends_on) - known
            if missing:
                raise ValueError(
                    f"static plan node {node.node_id} depends on unknown nodes: {sorted(missing)}"
                )
        logger.info(
            "[task][static-plan] parsed template=%s nodes=%s entry_bot_id=%s",
            raw["template_id"],
            [node.node_id for node in nodes],
            raw.get("entry_bot_id"),
        )
        # Kahn cycle check: static plans are DAGs, not planner input.
        remaining = {n.node_id: set(n.depends_on) for n in nodes}
        while remaining:
            ready = [key for key, deps in remaining.items() if not deps]
            if not ready:
                raise ValueError("static plan contains a dependency cycle")
            for key in ready:
                remaining.pop(key)
                for deps in remaining.values():
                    deps.discard(key)
        return cls(
            str(raw["template_id"]),
            raw.get("entry_bot_id"),
            dict(raw.get("input_schema") or {}),
            tuple(nodes),
            entry_name=raw.get("entry_name"),
        )

    @classmethod
    def from_file(
        cls,
        template_id: str,
        directory: Path,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> "StaticPlanDefinition":
        path = directory / f"{template_id}.yaml"
        if not path.is_file():
            raise ValueError(f"static plan not found: {template_id}")
        plan = cls.from_yaml(path.read_text(encoding="utf-8"), bindings=bindings)
        if plan.template_id != template_id:
            raise ValueError(f"static plan template_id mismatch: {plan.template_id}")
        return plan

    def validate_input(self, value: dict[str, Any]) -> None:
        logger.info(
            "[task][static-plan] validate input template=%s keys=%s",
            self.template_id,
            sorted(value),
        )
        for name, schema in self.input_schema.items():
            if schema.get("required") and (
                name not in value or value[name] in (None, "")
            ):
                raise ValueError(f"missing static plan input: {name}")
            if (
                name in value
                and schema.get("type") == "string"
                and not isinstance(value[name], str)
            ):
                raise ValueError(f"static plan input {name} must be string")

    def validate_bindings(self) -> None:
        # entry_bot_id 现不再用于 owner_bot_id 兜底(execute 内部选择模板);
        # 保留为模板元信息,不强制必填,不再纳入 binding 校验。
        missing = []
        for node in self.nodes:
            if node.node_type == "notify":
                # notify 终端通知节点无 bot 绑定(直走钉钉通道),不参与 bot binding 校验。
                continue
            if node.node_type == "collaboration":
                unbound = not node.bot_ids or any(not bot_id for bot_id in node.bot_ids)
            else:
                unbound = not node.bot_id
            if unbound:
                missing.append(node.node_id)
        if missing:
            logger.error(
                "[task][static-plan] bot binding validation failed template=%s missing=%s",
                self.template_id,
                missing,
            )
            raise TaskStateError(
                f"template bot binding missing: node={','.join(missing)}"
            )
        logger.info(
            "[task][static-plan] bot binding validation passed template=%s",
            self.template_id,
        )


@dataclass(frozen=True)
class StaticPlanReadiness:
    ready: tuple[TaskNode, ...] = ()
    skipped: tuple[TaskNode, ...] = ()


class StaticPlanRuntime:
    def __init__(self, definition: StaticPlanDefinition, inputs: dict[str, Any]):
        self.definition = definition
        self.inputs = inputs
        self.by_id = {n.node_id: n for n in definition.nodes}
        logger.info(
            "[task][static-plan-runtime] initialized template=%s input_keys=%s nodes=%s",
            definition.template_id,
            sorted(inputs),
            list(self.by_id),
        )

    def nodes(self, task_id: str, root_spec: TaskSpec) -> list[TaskNode]:
        result = []
        for item in self.definition.nodes:
            spec = TaskSpec(
                context=Context(
                    title=(item.title or item.task or item.name),
                    background=root_spec.context.background,
                    extend_props={"static_input": dict(item.input)},
                ),
                goal=Goal(objective=(item.title or item.name), acceptances=[]),
            )
            result.append(
                TaskNode(
                    node_id=item.node_id,
                    task_id=task_id,
                    status=Status.PENDING,
                    task_spec=spec,
                    run_info=RuntimeInfo(),
                    node_run_graph=None,  # type: ignore[arg-type]
                )
            )
        logger.info(
            "[task][static-plan-runtime] materialized task=%s nodes=%s",
            task_id,
            [node.node_id for node in result],
        )
        return result

    def ready(self, graph) -> StaticPlanReadiness:
        """Return executable and conditionally skipped nodes without persisting mutations.

        The graph service is the only state mutation gateway.  Readiness calculation
        may decorate in-memory dispatch metadata, but status/output changes are
        returned to the engine and persisted through ``update_task_node_info``.
        """
        done = {n.node_id for n in graph.tasks if n.status == Status.SUCCESS}
        logger.info(
            "[task][static-plan-runtime] readiness task=%s done=%s states=%s",
            graph.task_id,
            sorted(done),
            {n.node_id: n.status.value for n in graph.tasks if n.node_id in self.by_id},
        )
        ready: list[TaskNode] = []
        skipped: list[TaskNode] = []
        for node_id, definition in self.by_id.items():
            node = next((n for n in graph.tasks if n.node_id == node_id), None)
            if (
                node is None
                or node.status != Status.PENDING
                or node.run_info.extend_props.get("dispatching")
            ):
                continue
            node.run_info.extend_props["static_blocked"] = True
            if not set(definition.depends_on).issubset(done):
                continue
            if definition.enabled_when and not self._enabled(
                definition.enabled_when, graph
            ):
                skipped.append(node)
                continue
            self._decorate(node, definition, graph)
            node.run_info.extend_props["static_blocked"] = None
            ready.append(node)
        logger.info(
            "[task][static-plan-runtime] readiness result task=%s ready=%s skipped=%s waiting=%s",
            graph.task_id,
            [node.node_id for node in ready],
            [node.node_id for node in skipped],
            [
                node_id
                for node_id in self.by_id
                if node_id not in {n.node_id for n in ready}
                and node_id not in {n.node_id for n in skipped}
            ],
        )
        return StaticPlanReadiness(tuple(ready), tuple(skipped))

    def _decorate(
        self, node: TaskNode, definition: StaticPlanNodeDefinition, graph
    ) -> None:
        resolved = {
            key: self.resolve(value, graph) for key, value in definition.input.items()
        }
        node.task_spec.context.extend_props["static_input"] = resolved
        if definition.task:
            # V2 接力输入: 框架下发"# 接自 / ## 群组成(仅 collab) / ## 上游产出正文 / ## 本角色任务"。
            # 不做摘要/合成;rule(怎么分析/输出/接力交接)归各 bot 的 skill/rule,不在此注入。
            node.run_info.extend_props["execution_prompt"] = self._relay_instruction(
                definition, resolved, graph
            )
            node.task_spec.goal = Goal(
                objective=(definition.title or definition.name), acceptances=[]
            )
            logger.info(
                "[task][static-plan-runtime] relay v2 injected task=%s node=%s type=%s task_head=%r",
                graph.task_id,
                node.node_id,
                definition.node_type,
                definition.task[:40],
            )
        else:
            node.run_info.extend_props["execution_prompt"] = (
                f"{task_spec_instruction(node.task_spec)}\n输入: {resolved}"
            )
        node.run_info.extend_props["static_bot_id"] = definition.bot_id
        # 透传 owner_user_id 到 static 子节点: DirectDispatchStrategy 对 static 只设 static_bot_id、不设
        # owner_id,而 _dispatch_single_bot 经 compose_bot_identity 需 owner_id 才能拼出 BaaS 接受的复合
        # bot_id:owner(公网 secbaas 拒裸 bot_id → start_run_failed)。owner_id 取自 graph.extend_props(提交
        # 请求注入),与 root 一致;dispatcher 对 static result.owner_id=None 不覆盖,故此处设值保留到派发。
        _graph_props = getattr(graph, "extend_props", None) or {}
        _owner_user_id = _graph_props.get("owner_user_id")
        if _owner_user_id:
            node.run_info.extend_props["assignee_owner_id"] = str(_owner_user_id)
        logger.info(
            "[task][static-plan-runtime] node ready task=%s node=%s type=%s depends_on=%s bot_id=%s bot_ids=%s input_keys=%s",
            graph.task_id,
            node.node_id,
            definition.node_type,
            list(definition.depends_on),
            definition.bot_id,
            list(definition.bot_ids),
            sorted(resolved),
        )
        if definition.node_type == "collaboration":
            node.run_info.extend_props["static_group"] = {
                "bot_ids": [str(x) for x in definition.bot_ids if x],
                "collab_mode": "chat",
                "group_name": f"{graph.task_id}-{node.node_id}",
                "static_input": resolved,
            }

    def _upstream_identity(self, definition: StaticPlanNodeDefinition, graph) -> str:
        deps = list(definition.depends_on)
        if not deps:
            # 仅店庆模板需要把入口“接自”展示为实际触发方；其他静态模板
            # 保持既有入口元信息，避免改变既有剧本的接力语义。
            if self.definition.template_id == "merchant-operations-goal-to-plan":
                props = getattr(graph, "extend_props", {}) or {}
                cfg = props.get("execution_config") or {}
                trigger_id = (
                    props.get("trigger_bot_id")
                    or cfg.get("trigger_bot_id")
                    or props.get("owner_bot_id")
                )
                trigger_name = (
                    props.get("trigger_bot_name")
                    or cfg.get("trigger_bot_name")
                    or ("触发Bot" if trigger_id else self.definition.entry_name)
                )
                if trigger_id:
                    return f"{trigger_name or '触发Bot'}({trigger_id})"
            return f"{self.definition.entry_name or '入口'}({self.definition.entry_bot_id})"
        up = self.by_id.get(deps[0])
        if not up:
            return "上游"
        if up.node_type == "collaboration" and up.bot_ids:
            return f"{up.name}(driver {up.bot_ids[0]})"
        return f"{up.name}({up.bot_id})"

    def _upstream_output_text(self, resolved: dict[str, Any]) -> str:
        values = list(resolved.values())
        if len(values) == 1:
            v = values[0]
            if isinstance(v, str):
                return v
            # bot 上报 $.result 多为 {"summary": "...", "random": "..."};透传 summary 纯文本,
            # 不把 JSON 包裹塞给下游(下游看到的是自然语言产出正文,不是 {"summary":...} JSON)。
            if isinstance(v, dict) and "summary" in v:
                return str(v["summary"])
            return json.dumps(v, ensure_ascii=False, default=str)
        if not values:
            return "(无)"
        return json.dumps(resolved, ensure_ascii=False, default=str)

    def _relay_instruction(
        self, definition: StaticPlanNodeDefinition, resolved: dict[str, Any], graph
    ) -> str:
        parts = [f"# 接自:{self._upstream_identity(definition, graph)}"]
        if definition.node_type == "collaboration" and definition.bot_names:
            ids = definition.bot_ids
            names = definition.bot_names
            group_lines = ["## 群组成"]
            if ids and names:
                group_lines.append(f"- {names[0]}({ids[0]}) = driver / 总结者")
                for nm, bid in zip(names[1:], ids[1:]):
                    group_lines.append(f"- {nm}({bid})")
            parts.append("\n".join(group_lines))
        parts.append("## 上游产出正文")
        parts.append(self._upstream_output_text(resolved))
        parts.append(
            "## 本群任务"
            if definition.node_type == "collaboration"
            else "## 本角色任务"
        )
        parts.append(definition.task)
        return "\n".join(parts)

    def resolve(self, value: Any, graph) -> Any:
        if not isinstance(value, str) or not value.startswith("$."):
            return value
        parts = value[2:].split(".")
        if parts == ["input", "okr"]:
            return self.inputs.get("okr")
        if len(parts) >= 3 and parts[1] == "output":
            node = next((n for n in graph.tasks if n.node_id == parts[0]), None)
            current: Any = node.run_info.output if node else None
            for part in parts[2:]:
                current = current.get(part) if isinstance(current, dict) else None
            return current
        return None

    def _enabled(self, expression: str, graph) -> bool:
        # Static-plan expressions are intentionally small and declarative.
        if "== true" in expression:
            left = expression.split("==", 1)[0].strip()
            return self.resolve(left, graph) is True
        return True
