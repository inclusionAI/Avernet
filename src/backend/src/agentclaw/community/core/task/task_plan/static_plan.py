"""Validated, repository-owned static-plan task plan definitions."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from agentclaw.community.core.task.domain.errors import TaskStateError


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
    def from_yaml(cls, text: str, *, bindings: Mapping[str, str] | None = None) -> "StaticPlanDefinition":
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
            nodes.append(StaticPlanNodeDefinition(
                node_id=node_id, name=str(item.get("name", node_id)),
                node_type=str(item.get("type", "bot")),
                depends_on=tuple(str(x) for x in item.get("depends_on", [])),
                bot_id=item.get("bot_id"),
                bot_ids=tuple(collab.get("bot_ids", [])),
                input=dict(item.get("input") or {}), output=dict(item.get("output") or {}),
                enabled_when=(item.get("enabled_when") or {}).get("expression")
                    if isinstance(item.get("enabled_when"), dict) else None,
                # V2 接力输入(本角色任务 + 群组成角色名);缺省为空(老模板走旧行为,不变)。
                task=str(item.get("task") or "").strip(),
                title=str(item.get("title") or "").strip(),
                bot_names=tuple(str(x) for x in collab.get("bot_names", [])),
            ))
        known = {n.node_id for n in nodes}
        for node in nodes:
            missing = set(node.depends_on) - known
            if missing:
                raise ValueError(f"static plan node {node.node_id} depends on unknown nodes: {sorted(missing)}")
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
        return cls(str(raw["template_id"]), raw.get("entry_bot_id"), dict(raw.get("input_schema") or {}), tuple(nodes), entry_name=raw.get("entry_name"))

    @classmethod
    def from_file(cls, template_id: str, directory: Path, *, bindings: Mapping[str, str] | None = None) -> "StaticPlanDefinition":
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
            if schema.get("required") and (name not in value or value[name] in (None, "")):
                raise ValueError(f"missing static plan input: {name}")
            if name in value and schema.get("type") == "string" and not isinstance(value[name], str):
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
            raise TaskStateError(f"template bot binding missing: node={','.join(missing)}")
        logger.info(
            "[task][static-plan] bot binding validation passed template=%s",
            self.template_id,
        )
