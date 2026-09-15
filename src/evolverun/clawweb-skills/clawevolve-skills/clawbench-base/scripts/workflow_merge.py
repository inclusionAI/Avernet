#!/usr/bin/env python3
"""
workflow_merge.py — 通用 Workflow 多节点 Session 合并工具

将 workflow 目录下每个 workflow-id 子目录中的多个 skill JSONL 文件
合并为单个 session transcript，供 benchmark.py / grade_task() 评分。

输出格式规范（v2）：
  第 1 行：__manifest__ event — 包含格式版本、节点列表等元信息
  后续行：按节点顺序排列的 event，每条 event 含 __node__ 字段
  节点之间：__node_boundary__ event 作为分隔标记

设计原则：
  - 自动发现节点：扫描目录中的 *-attempt-*.jsonl 文件，按文件名排序确定节点顺序
  - 也可通过 --nodes 显式指定节点顺序
  - 注入 __node__ 字段到每行，标记所属节点
  - 第一个节点保留完整 session 头（type=session/model_change/thinking_level_change）
  - 后续节点去除重复的 session 头，只保留 message 和 custom 事件
  - 在节点之间插入 boundary marker（type=__node_boundary__）

用法：
  # 自动发现节点，合并单个 session
  python3 workflow_merge.py --session-dir ./workflow-dispatcher/<uuid> --output merged/<uuid>.jsonl

  # 批量合并
  python3 workflow_merge.py --batch ./workflow-dispatcher/ --output-dir ./merged/

  # 显式指定节点顺序（适用于所有 workflow）
  python3 workflow_merge.py --batch ./workflow-dispatcher/ --output-dir ./merged/ \
      --nodes context-enrichment intent-recognition task-dispatch

  # 指定文件名模式（默认 {node}-attempt-1.jsonl）
  python3 workflow_merge.py --batch ./my-workflow/ --output-dir ./merged/ \
      --pattern "{node}-attempt-1.jsonl"

复用到其他 workflow：
  只需指向新的 workflow 数据目录，脚本会自动发现节点文件。
  如果节点顺序不是字母序，用 --nodes 显式指定。
"""

import json
import os
import re
import sys
import argparse
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# 格式版本号 — 与 generate_eval_template.py 中的 TRANSCRIPT_FORMAT_SPEC 同步
MERGE_FORMAT_VERSION = "2.1"
# 默认文件名模式：{node}-attempt-{N}.jsonl
DEFAULT_PATTERN = "{node}-attempt-1.jsonl"
# Session 头部事件类型（只在第一个节点保留）
SESSION_HEADER_TYPES = {"session", "model_change", "thinking_level_change"}


def discover_nodes(session_dir: Path, pattern: str = DEFAULT_PATTERN) -> List[str]:
    """
    自动发现 session 目录中的节点名称。

    扫描匹配 *-attempt-*.jsonl 的文件，提取节点名并按字母序排列。
    """
    node_regex = re.compile(r"^(.+)-attempt-\d+\.jsonl$")
    nodes = []
    for f in sorted(session_dir.iterdir()):
        if not f.is_file():
            continue
        m = node_regex.match(f.name)
        if m:
            node_name = m.group(1)
            # 排除 trajectory 文件
            if ".trajectory" not in f.name:
                nodes.append(node_name)
    # 去重保序
    seen = set()
    unique = []
    for n in nodes:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return unique


def parse_workflow_dag(yaml_path: Path) -> Dict[str, List[str]]:
    """
    从 workflow YAML 中解析节点 DAG 依赖关系。

    优先使用 pyyaml 解析（兼容所有 YAML 格式），
    如果 pyyaml 不可用则回退到正则解析。

    Returns:
        dict: {node_id: [依赖的节点id列表]}
    """
    text = yaml_path.read_text(encoding="utf-8")

    if HAS_YAML:
        return _parse_dag_pyyaml(text)
    else:
        print("  WARNING: pyyaml not installed, falling back to regex parsing", file=sys.stderr)
        return _parse_dag_regex(text)


def _parse_dag_pyyaml(yaml_text: str) -> Dict[str, List[str]]:
    """使用 pyyaml 解析 DAG。"""
    data = yaml.safe_load(yaml_text)

    # 兼容两种格式：
    # 1. 顶层是 list（直接是节点列表）
    # 2. 顶层是 dict，节点在 data["nodes"] 下
    if isinstance(data, list):
        nodes = data
    elif isinstance(data, dict):
        nodes = data.get("nodes", [])
    else:
        return {}

    dag: Dict[str, List[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not node_id:
            continue
        deps = node.get("dependsOn", [])
        # dependsOn 可能是 None（YAML 中写 dependsOn: 但没值）
        if deps is None:
            deps = []
        # 确保是 list
        if isinstance(deps, str):
            deps = [deps]
        dag[node_id] = deps

    return dag


def _parse_dag_regex(yaml_text: str) -> Dict[str, List[str]]:
    """正则 fallback：仅支持 block style 且顶格的 `- id:` 格式。"""
    node_blocks = re.split(r'\n(?=-\s*id:)', '\n' + yaml_text)

    dag: Dict[str, List[str]] = {}
    for block in node_blocks:
        id_m = re.search(r'-\s*id:\s*(\S+)', block)
        if not id_m:
            continue
        node_id = id_m.group(1)
        # 解析 dependsOn 列表（block style）
        deps_m = re.search(r'dependsOn:\s*\n((?:\s+-\s+\S+\n?)+)', block)
        if deps_m:
            deps = re.findall(r'-\s+(\S+)', deps_m.group(1))
        else:
            # 尝试 flow style: dependsOn: [a, b]
            flow_m = re.search(r'dependsOn:\s*\[([^\]]*)\]', block)
            if flow_m:
                items = flow_m.group(1).strip()
                deps = [x.strip() for x in items.split(",") if x.strip()] if items else []
            else:
                deps = []
        dag[node_id] = deps

    return dag


def get_yaml_node_order(yaml_path: Path) -> List[str]:
    """
    从 workflow YAML 中提取节点声明顺序（nodes 数组中的 id 顺序）。

    用于拓扑排序时的 tie-breaking：当多个节点入度相同时，
    按 YAML 声明顺序排列，保证确定性且符合编排意图。

    Returns:
        按声明顺序排列的节点 id 列表。解析失败时返回空列表。
    """
    text = yaml_path.read_text(encoding="utf-8")

    if HAS_YAML:
        data = yaml.safe_load(text)
        if isinstance(data, list):
            node_list = data
        elif isinstance(data, dict):
            node_list = data.get("nodes", [])
        else:
            return []
        return [n["id"] for n in node_list if isinstance(n, dict) and "id" in n]
    else:
        # regex fallback：匹配顶格或缩进的 `- id:` 或 `id:`
        ids = re.findall(r"^\s*-?\s*id:\s*(\S+)", text, re.MULTILINE)
        return ids


def get_timestamp_order(session_dir: Path, nodes: List[str], pattern: str = DEFAULT_PATTERN) -> List[str]:
    """
    从各节点 JSONL 文件的第一条 event 的 timestamp 字段推断执行顺序。

    Returns:
        按 timestamp 排序的节点列表。无法获取 timestamp 的节点排在末尾。
    """
    node_timestamps: List[Tuple[str, str]] = []

    for node in nodes:
        filepath = session_dir / pattern.format(node=node)
        if not filepath.exists():
            node_timestamps.append((node, ""))
            continue

        timestamp = ""
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    ts = obj.get("timestamp", "")
                    if ts:
                        timestamp = ts
                        break
                except json.JSONDecodeError:
                    continue

        node_timestamps.append((node, timestamp))

    # 按 timestamp 排序（空 timestamp 排末尾）
    node_timestamps.sort(key=lambda x: x[1] if x[1] else "z" * 50)
    return [n for n, _ in node_timestamps]


def validate_order(dag_order: List[str], ts_order: List[str], quiet: bool = False) -> bool:
    """
    交叉验证 DAG 拓扑排序和 timestamp 排序是否一致。

    不要求完全相同（并行节点顺序可能不同），
    只检查是否存在拓扑违反：即 DAG 中 A→B 但 timestamp 中 B 在 A 前面。

    Returns:
        True 如果一致，False 如果有冲突
    """
    if dag_order == ts_order:
        return True

    # 构建 DAG order 的位置索引
    dag_pos = {node: i for i, node in enumerate(dag_order)}
    ts_pos = {node: i for i, node in enumerate(ts_order)}

    # 检查：对于 DAG 中每对有序关系 (A before B)，timestamp 中是否也是 A before B
    conflicts = []
    for i, node_a in enumerate(dag_order):
        for node_b in dag_order[i + 1:]:
            # DAG 说 node_a 在 node_b 前面
            if node_a in ts_pos and node_b in ts_pos:
                if ts_pos[node_a] > ts_pos[node_b]:
                    conflicts.append((node_a, node_b))

    if conflicts and not quiet:
        print(f"  WARNING: DAG order and timestamp order conflict:", file=sys.stderr)
        print(f"    DAG order:       {dag_order}", file=sys.stderr)
        print(f"    Timestamp order: {ts_order}", file=sys.stderr)
        for a, b in conflicts[:3]:  # 最多显示 3 个冲突
            print(f"    Conflict: DAG says {a} → {b}, but timestamp says {b} first", file=sys.stderr)

    return len(conflicts) == 0


def topo_sort_nodes(
    dag: Dict[str, List[str]],
    available_nodes: List[str],
    yaml_order: Optional[List[str]] = None,
) -> List[str]:
    """
    对 available_nodes 按 DAG 拓扑排序（Kahn's algorithm）。

    只保留 available_nodes 中存在的节点，忽略 DAG 中不在 available_nodes 的依赖。
    如果存在环或无法排序，回退到原始顺序。

    Args:
        dag: 完整 DAG {node_id: [依赖列表]}
        available_nodes: 实际存在 JSONL 文件的节点列表

    Returns:
        拓扑排序后的节点列表
    """
    node_set = set(available_nodes)

    # 构建仅包含 available_nodes 的子图
    # in_degree[n] = 该节点在子图中的入度
    in_degree: Dict[str, int] = {n: 0 for n in available_nodes}
    # adjacency: 正向边 (依赖 → 被依赖者)
    # 如果 B dependsOn A，则 A → B（A 完成后 B 才能执行）
    adj: Dict[str, List[str]] = {n: [] for n in available_nodes}

    def _resolve_transitive_deps(node_id: str, dag: Dict[str, List[str]], node_set: set) -> set:
        """递归穿透非 available 节点，找到传递依赖的 available 节点。

        例如 task-dispatch → task-generation(cli-script) → intent-recognition(agent)，
        应返回 {intent-recognition}，保证 task-dispatch 排在 intent-recognition 之后。
        """
        result = set()
        visited = set()
        stack = list(dag.get(node_id, []))
        while stack:
            dep = stack.pop()
            if dep in visited:
                continue
            visited.add(dep)
            if dep in node_set:
                result.add(dep)
            else:
                # 非 available 节点（如 cli-script / done），继续穿透找它的依赖
                stack.extend(dag.get(dep, []))
        return result

    for node in available_nodes:
        deps = _resolve_transitive_deps(node, dag, node_set)
        for dep in deps:
            # dep → node（dep 完成后 node 才执行）
            adj[dep].append(node)
            in_degree[node] += 1

    # tie-breaking key：yaml 声明顺序优先
    def _sort_key(node_name: str) -> int:
        if yaml_order and node_name in yaml_order:
            return yaml_order.index(node_name)
        return 999

    # Kahn's algorithm — 初始队列按 yaml_order 排序
    queue = deque(sorted(
        [n for n in available_nodes if in_degree[n] == 0],
        key=_sort_key,
    ))
    sorted_nodes = []

    while queue:
        # 如果多个节点入度为 0，按 yaml_order 选择（稳定排序）
        current = queue.popleft()
        sorted_nodes.append(current)
        newly_ready = []
        for neighbor in adj[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                newly_ready.append(neighbor)
        for n in sorted(newly_ready, key=_sort_key):
            queue.append(n)

    # 如果排序结果不完整（有环），回退
    if len(sorted_nodes) != len(available_nodes):
        fallback = yaml_order if yaml_order else available_nodes
        print(f"  WARNING: DAG has cycle, falling back to {'yaml' if yaml_order else 'alphabetical'} order", file=sys.stderr)
        return [n for n in fallback if n in node_set]

    return sorted_nodes


def get_node_filepath(session_dir: Path, node: str, pattern: str = DEFAULT_PATTERN) -> Path:
    """根据模式获取节点文件路径。"""
    filename = pattern.format(node=node)
    return session_dir / filename


def _get_trace_node_output(node_id: str, workflow_trace: dict) -> Any:
    """Extract a node's structured output from workflow_trace.node_executions."""
    for ne in workflow_trace.get("node_executions", []):
        if ne.get("node_id") == node_id:
            output = ne.get("output")
            if output is not None:
                return output
    return None


def _build_node_synthetic_event(node_id: str, workflow_trace: dict) -> dict | None:
    """Build a __node_synthetic__ event for a node that has no session file.

    Uses workflow_trace.node_executions to construct an event with the node's
    output, executor_type, and status. This allows grade() to see cli-script,
    done, and unfound subagent nodes in the merged transcript.
    """
    for ne in workflow_trace.get("node_executions", []):
        if ne.get("node_id") == node_id:
            event = {
                "type": "__node_synthetic__",
                "__node__": node_id,
                "executor_type": ne.get("executor_type", ""),
                "status": ne.get("status", ""),
                "__node_output__": ne.get("output"),
                "output_keys": ne.get("output_keys", []),
            }
            # Use started_at as timestamp if available
            started = ne.get("started_at")
            if started:
                event["timestamp"] = _format_trace_timestamp(started)
            return event
    return None


def _format_trace_timestamp(value: Any) -> str:
    """Format a workflow_trace timestamp (ms epoch or ISO string) to ISO-8601."""
    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(value / 1000.0 if value > 1e12 else value, tz=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
        except (OSError, ValueError, OverflowError):
            return ""
    if isinstance(value, str):
        return value
    return ""


def _build_workflow_start_event(workflow_trace: dict) -> dict | None:
    """Build a __workflow_start__ event with DAG structure and trigger info."""
    event = {
        "type": "__workflow_start__",
        "workflow_id": workflow_trace.get("workflow_id", ""),
        "flow_id": workflow_trace.get("flow_id", ""),
        "goal": workflow_trace.get("goal", ""),
        "dag": [],
    }
    # Build compact DAG representation
    for d_entry in workflow_trace.get("dag", []):
        # Normalize executor: trace.dag[].executor may be dict after _parse_state_json
        exe = d_entry.get("executor", "")
        if isinstance(exe, dict):
            exe = exe.get("type") or exe.get("name") or str(exe)
        event["dag"].append({
            "node": d_entry.get("node", ""),
            "executor": exe,
            "deps": d_entry.get("deps", []),
        })
    started = workflow_trace.get("started_at")
    if started:
        event["timestamp"] = _format_trace_timestamp(started)
    return event


def _build_workflow_end_event(workflow_trace: dict) -> dict | None:
    """Build a __workflow_end__ event with final status and outputs."""
    event = {
        "type": "__workflow_end__",
        "status": workflow_trace.get("status", ""),
        "workflow_outputs": workflow_trace.get("workflow_outputs", {}),
    }
    # Calculate duration
    started = workflow_trace.get("started_at")
    ended = workflow_trace.get("ended_at")
    if started and ended:
        try:
            s = _parse_iso_timestamp(started)
            e = _parse_iso_timestamp(ended)
            if s is not None and e is not None:
                event["duration_s"] = round((e - s).total_seconds(), 1)
        except Exception:
            pass
    if ended:
        event["timestamp"] = _format_trace_timestamp(ended)
    return event


def _parse_iso_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string or ms epoch into a datetime."""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000.0 if value > 1e12 else value, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return None


def merge_session(
    session_dir: Path,
    output_path: Path,
    nodes: Optional[List[str]] = None,
    dag: Optional[Dict[str, List[str]]] = None,
    yaml_order: Optional[List[str]] = None,
    pattern: str = DEFAULT_PATTERN,
    insert_boundary: bool = True,
    strip_headers: bool = True,
    workflow_trace: Optional[dict] = None,
    external_sessions: Optional[Dict[str, str]] = None,
) -> dict:
    """
    合并一个 session 目录的多个节点文件为单个 JSONL。

    Args:
        session_dir: workflow-id 目录路径
        output_path: 输出文件路径
        nodes: 节点名称列表（按执行顺序）。None 则自动发现。
        dag: 从 workflow YAML 解析的 DAG 依赖关系。提供时对自动发现的节点做拓扑排序。
        yaml_order: workflow YAML 中节点的声明顺序。DAG 拓扑排序时用于并行节点 tie-breaking；
                    DAG 为空时用作主排序（优先级高于时间戳）。
        pattern: 文件名模式，{node} 会被替换为节点名
        insert_boundary: 是否在节点之间插入 boundary marker
        strip_headers: 是否去除后续节点的 session 头部事件
        workflow_trace: workflow 流转数据 dict（v2.1+），注入 __manifest__
        external_sessions: {node_id: 外部 JSONL 路径}，用于 session 文件不在
                          session_dir 下的节点（如 subagent 独立 session）

    Returns:
        合并统计信息 dict
    """
    if nodes is None:
        nodes = discover_nodes(session_dir, pattern)
        # Merge in external session nodes (e.g. subagent nodes whose session
        # files are outside session_dir) so DAG sort sees them.
        if external_sessions:
            for ext_node in external_sessions:
                if ext_node not in nodes:
                    nodes.append(ext_node)
        # Also add nodes from workflow_trace DAG that have no session files
        # (cli-script, done, and subagent nodes without external_paths).
        # These will be injected as __node_synthetic__ events later.
        if workflow_trace and isinstance(workflow_trace, dict):
            for d_entry in workflow_trace.get("dag", []):
                dag_node = d_entry.get("node", "")
                if dag_node and dag_node not in nodes:
                    nodes.append(dag_node)
        if dag and nodes:
            # DAG 拓扑排序（yaml_order 用于并行节点 tie-breaking）
            nodes = topo_sort_nodes(dag, nodes, yaml_order)
            # 交叉验证：用 timestamp 顺序验证 DAG 排序
            ts_order = get_timestamp_order(session_dir, nodes, pattern)
            validate_order(nodes, ts_order)
        elif yaml_order and nodes:
            # 无 DAG → 按 yaml 声明顺序排，不在 yaml 里的放末尾
            nodes = sorted(nodes, key=lambda n: yaml_order.index(n) if n in yaml_order else 999)
        elif nodes:
            # 最后兜底：时间戳顺序
            nodes = get_timestamp_order(session_dir, nodes, pattern)

    if not nodes:
        return {"error": "no nodes found", "events": 0}

    merged_events = []
    stats = {"nodes": [], "total_events": 0, "synthetic_nodes": []}

    # ── Pre-scan: determine which nodes have real session files ──
    nodes_with_files = set()
    for node_name in nodes:
        if external_sessions and node_name in external_sessions:
            filepath = Path(external_sessions[node_name])
        else:
            filepath = get_node_filepath(session_dir, node_name, pattern)
        if filepath.exists():
            nodes_with_files.add(node_name)

    # ── Inject __workflow_start__ event (from workflow_trace) ──
    if workflow_trace and isinstance(workflow_trace, dict):
        wf_start_event = _build_workflow_start_event(workflow_trace)
        if wf_start_event:
            merged_events.append(wf_start_event)

    # ── Process ALL nodes in DAG order (real + synthetic interleaved) ──
    synthetic_injected = set()
    real_node_idx = 0
    last_inserted_node = "__workflow_start__"

    for i, node_name in enumerate(nodes):
        has_file = node_name in nodes_with_files
        is_external = external_sessions and node_name in external_sessions

        if has_file or is_external:
            # ── Real session file exists ──
            if is_external:
                filepath = Path(external_sessions[node_name])
            else:
                filepath = get_node_filepath(session_dir, node_name, pattern)

            if not filepath.exists():
                # File was expected but disappeared
                stats["nodes"].append({"name": node_name, "events": 0, "status": "missing"})
                continue

            node_events = []
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # 后续节点去除重复的 session 头
                    if strip_headers and real_node_idx > 0:
                        event_type = obj.get("type", "")
                        if event_type in SESSION_HEADER_TYPES:
                            continue

                    # 注入 __node__ 标记
                    obj["__node__"] = node_name
                    # Inject __node_output__ from workflow_trace into the first
                    # assistant message for this node (so grade() can access it)
                    if workflow_trace and isinstance(workflow_trace, dict):
                        node_output = _get_trace_node_output(node_name, workflow_trace)
                        if node_output is not None:
                            # Attach to the first event only
                            has_output_already = any(
                                e.get("__node__") == node_name and "__node_output__" in e
                                for e in node_events
                            )
                            if not has_output_already:
                                obj["__node_output__"] = node_output

                    node_events.append(obj)

            # Insert boundary before this node's events
            if insert_boundary and node_events:
                boundary = {
                    "type": "__node_boundary__",
                    "__node__": node_name,
                    "__prev_node__": last_inserted_node,
                    "__node_index__": i,
                }
                merged_events.append(boundary)

            merged_events.extend(node_events)
            stats["nodes"].append({"name": node_name, "events": len(node_events), "status": "ok"})
            last_inserted_node = node_name
            real_node_idx += 1

        else:
            # ── No session file → inject __node_synthetic__ ──
            if workflow_trace and isinstance(workflow_trace, dict):
                syn_event = _build_node_synthetic_event(node_name, workflow_trace)
                if syn_event:
                    # Insert boundary before synthetic event
                    if insert_boundary:
                        boundary = {
                            "type": "__node_boundary__",
                            "__node__": node_name,
                            "__prev_node__": last_inserted_node,
                            "__node_index__": i,
                        }
                        merged_events.append(boundary)

                    merged_events.append(syn_event)
                    synthetic_injected.add(node_name)
                    stats["synthetic_nodes"].append(node_name)
                    stats["nodes"].append({"name": node_name, "events": 1, "status": "synthetic"})
                    last_inserted_node = node_name
                    continue

            # No trace data either → mark as missing
            stats["nodes"].append({"name": node_name, "events": 0, "status": "missing"})

    # ── Inject __workflow_end__ event (from workflow_trace) ──
    if workflow_trace and isinstance(workflow_trace, dict):
        wf_end_event = _build_workflow_end_event(workflow_trace)
        if wf_end_event:
            merged_events.append(wf_end_event)

    # 构造 manifest（第一行元信息）
    # Only include nodes that actually executed (status="ok"), not synthetic or
    # skipped branch nodes. This prevents grade() from penalizing unexecuted
    # branches in BRANCH checks when the workflow took a different path.
    executed_nodes = []
    for n_stat in stats["nodes"]:
        if n_stat["status"] == "ok":
            executed_nodes.append(n_stat["name"])
    manifest = {
        "type": "__manifest__",
        "format_version": MERGE_FORMAT_VERSION,
        "nodes": executed_nodes,
        "node_count": len(executed_nodes),
        "total_events": len(merged_events),
        "synthetic_nodes": stats["synthetic_nodes"],
        "node_count_total": sum(1 for n_stat in stats["nodes"] if n_stat["status"] in ("ok", "synthetic")),
    }
    # Inject workflow trace when provided (format_version ≥ 2.1)
    if workflow_trace and isinstance(workflow_trace, dict):
        manifest["workflow_trace"] = workflow_trace

    # 写入输出
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        # 第一行：manifest
        f.write(json.dumps(manifest, ensure_ascii=False) + "\n")
        # 后续行：events
        for event in merged_events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    stats["total_events"] = len(merged_events) + 1  # +1 for manifest
    return stats


def batch_merge(
    base_dir: Path,
    output_dir: Path,
    nodes: Optional[List[str]] = None,
    dag: Optional[Dict[str, List[str]]] = None,
    yaml_order: Optional[List[str]] = None,
    pattern: str = DEFAULT_PATTERN,
    insert_boundary: bool = True,
    strip_headers: bool = True,
) -> List[dict]:
    """批量合并所有 session 目录。"""
    # 识别 session 目录（UUID 格式或包含 attempt JSONL 的目录）
    session_dirs = []
    for d in sorted(base_dir.iterdir()):
        if not d.is_dir():
            continue
        # 跳过输出目录和隐藏目录
        if d.name.startswith(".") or d.name == output_dir.name:
            continue
        # 检查是否包含 attempt JSONL 文件
        has_attempt = any(
            f.name.endswith(".jsonl") and "attempt" in f.name and "trajectory" not in f.name
            for f in d.iterdir() if f.is_file()
        )
        if has_attempt:
            session_dirs.append(d)

    results = []
    for session_dir in session_dirs:
        output_path = output_dir / f"{session_dir.name}.jsonl"
        stats = merge_session(
            session_dir=session_dir,
            output_path=output_path,
            nodes=nodes,
            dag=dag,
            yaml_order=yaml_order,
            pattern=pattern,
            insert_boundary=insert_boundary,
            strip_headers=strip_headers,
        )
        stats["session_id"] = session_dir.name
        results.append(stats)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="通用 Workflow 多节点 Session 合并工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 自动发现节点，批量合并（字母序）
  python3 workflow_merge.py --batch ./workflow-dispatcher/ --output-dir ./merged/

  # 按 workflow DAG 拓扑排序合并（推荐）
  python3 workflow_merge.py --batch ./workflow-dispatcher/ --output-dir ./merged/ \\
      --workflow-yaml ./my-workflow.yaml

  # 显式指定节点顺序
  python3 workflow_merge.py --batch ./workflow-dispatcher/ --output-dir ./merged/ \\
      --nodes context-enrichment intent-recognition task-dispatch

  # 合并单个 session
  python3 workflow_merge.py --session-dir ./workflow-dispatcher/00e9dcaa-... --output ./out.jsonl
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--session-dir", type=Path, help="单个 session 目录路径")
    mode.add_argument("--batch", type=Path, help="批量模式：包含多个 session 子目录的父目录")

    parser.add_argument("--output", type=Path, help="单 session 模式的输出文件路径")
    parser.add_argument("--output-dir", type=Path, default=Path("merged"),
                        help="批量模式的输出目录 (default: merged/)")
    parser.add_argument("--nodes", nargs="+",
                        help="显式指定节点名称和顺序（不指定则自动发现）")
    parser.add_argument("--workflow-yaml", type=Path,
                        help="Workflow YAML 文件路径，用于按 DAG 拓扑排序节点（优先级低于 --nodes）")
    parser.add_argument("--pattern", default=DEFAULT_PATTERN,
                        help=f"节点文件名模式 (default: {DEFAULT_PATTERN})")
    parser.add_argument("--no-boundary", action="store_true",
                        help="不插入节点边界标记")
    parser.add_argument("--keep-headers", action="store_true",
                        help="保留后续节点的 session 头部事件")
    parser.add_argument("--quiet", action="store_true", help="静默模式")
    parser.add_argument("--inject-trace", action="store_true",
                        help="Build workflow_trace from local sources and inject into manifest")

    args = parser.parse_args()

    # 解析 workflow YAML 获取 DAG + 节点声明顺序（如果提供）
    dag = None
    yaml_order = None
    if args.workflow_yaml:
        if not args.workflow_yaml.exists():
            print(f"ERROR: workflow YAML not found: {args.workflow_yaml}", file=sys.stderr)
            sys.exit(1)
        dag = parse_workflow_dag(args.workflow_yaml)
        yaml_order = get_yaml_node_order(args.workflow_yaml)
        if not args.quiet:
            print(f"  DAG loaded: {len(dag)} nodes, yaml order: {len(yaml_order)} nodes from {args.workflow_yaml.name}")

    if args.session_dir:
        # 单 session 模式
        output = args.output or Path(f"merged/{args.session_dir.name}.jsonl")
        stats = merge_session(
            session_dir=args.session_dir,
            output_path=output,
            nodes=args.nodes,
            dag=dag,
            yaml_order=yaml_order,
            pattern=args.pattern,
            insert_boundary=not args.no_boundary,
            strip_headers=not args.keep_headers,
        )
        if not args.quiet:
            print(f"Merged: {args.session_dir.name}")
            print(f"  Nodes: {[n['name'] for n in stats.get('nodes', [])]}")
            print(f"  Events: {stats['total_events']}")
            print(f"  Output: {output}")
    else:
        # 批量模式
        results = batch_merge(
            base_dir=args.batch,
            output_dir=args.output_dir,
            nodes=args.nodes,
            dag=dag,
            yaml_order=yaml_order,
            pattern=args.pattern,
            insert_boundary=not args.no_boundary,
            strip_headers=not args.keep_headers,
        )
        if not args.quiet:
            ok = sum(1 for r in results if r["total_events"] > 0)
            print(f"Batch merge complete: {ok}/{len(results)} sessions")
            if results:
                sample = results[0]
                print(f"  Nodes: {[n['name'] for n in sample.get('nodes', [])]}")
                total_events = sum(r["total_events"] for r in results)
                print(f"  Total events: {total_events}")
                print(f"  Output dir: {args.output_dir}")


if __name__ == "__main__":
    main()
