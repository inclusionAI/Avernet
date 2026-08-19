#!/opt/conda/bin/python3
"""
Langfuse 对话记录查询脚本

安全说明：
- 优先从已编译的安全模块（_secrets.so）获取密钥（最安全）
- 其次从环境变量 LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY 获取
- 禁止在代码中硬编码密钥
- 支持从 .env 文件加载配置

使用方式：
    python langfuse_query.py --session-id "xxx" --user-id "103892" --days 0.02
"""

import os
import sys
import json
import argparse
import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

# Langfuse SDK
try:
    from langfuse import Langfuse
except ImportError:
    print("错误: 请安装 langfuse SDK: pip install langfuse", file=sys.stderr)
    sys.exit(1)


def _load_secrets_from_compiled_module():
    """
    尝试从已编译的安全模块加载密钥（最安全的方式）

    安全模块通过 Cython 编译，用户无法直接读取密钥
    返回: (public_key, secret_key, host, project_id) 或 None

    支持两种 .so 文件命名：
    - _secrets.cpython-312-x86_64-linux-gnu.so (Linux)
    - _secrets.cpython-312-darwin.so (macOS)
    """
    try:
        # 当前脚本目录（优先）
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # 检查是否存在编译后的 .so 文件
        so_patterns = [
            "_secrets.cpython-312-x86_64-linux-gnu.so",  # Linux
            "_secrets.cpython-312-darwin.so",             # macOS
            "_secrets.so",                                 # 通用名
        ]

        so_file = None
        for pattern in so_patterns:
            candidate = os.path.join(script_dir, pattern)
            if os.path.exists(candidate):
                so_file = candidate
                break

        if not so_file:
            return None

        # 临时添加脚本目录到 sys.path
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        try:
            import _secrets

            # 优先使用 get_langfuse_config() 函数（推荐方式）
            if hasattr(_secrets, 'get_langfuse_config'):
                config = _secrets.get_langfuse_config()
                return (
                    config.get("public_key"),
                    config.get("secret_key"),
                    config.get("host"),
                    config.get("project_id")
                )

            # 兼容旧版本：直接访问属性
            elif hasattr(_secrets, 'LANGFUSE_PUBLIC_KEY'):
                return (
                    _secrets.LANGFUSE_PUBLIC_KEY,
                    _secrets.LANGFUSE_SECRET_KEY,
                    getattr(_secrets, "LANGFUSE_HOST", "https://langfuse.antfin.com"),
                    getattr(_secrets, "LANGFUSE_PROJECT_ID", None)
                )

            return None

        except ImportError as e:
            print(f"导入 _secrets 模块失败: {e}", file=sys.stderr)
            return None

    except Exception as e:
        print(f"加载安全模块失败: {e}", file=sys.stderr)
        return None


# 尝试从安全模块加载密钥
_secrets = _load_secrets_from_compiled_module()

# 密钥优先级：安全模块 > 环境变量
if _secrets:
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST, LANGFUSE_PROJECT_ID = _secrets
    # 如果安全模块没有返回 host，使用默认值
    if not LANGFUSE_HOST:
        LANGFUSE_HOST = "https://aivision.alipay.com"
else:
    LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
    LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
    LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://aivision.alipay.com")
    LANGFUSE_PROJECT_ID = os.environ.get("LANGFUSE_PROJECT_ID")


@dataclass
class ConversationSession:
    """会话信息"""
    session_id: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_turns: int = 0
    turns: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class ConversationTurn:
    """对话轮次"""
    role: str
    content: str
    tool_calls: list = field(default_factory=list)
    timestamp: Optional[datetime] = None


def get_langfuse_client() -> Langfuse:
    """获取 Langfuse 客户端"""
    if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
        raise ValueError(
            "Langfuse 密钥未配置！\n"
            "请设置环境变量：\n"
            "  export LANGFUSE_PUBLIC_KEY='your-public-key'\n"
            "  export LANGFUSE_SECRET_KEY='your-secret-key'\n"
            "或在脚本同目录下创建 .env 文件"
        )

    return Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST
    )


def _safe_get_metadata(trace: dict) -> dict:
    """
    安全获取 metadata 字典

    Langfuse 返回的 metadata 可能是：
    - dict（正常情况）
    - str（JSON 字符串）
    - None
    """
    raw_metadata = trace.get("metadata", {})
    if raw_metadata is None:
        return {}
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if isinstance(raw_metadata, str):
        try:
            import json
            return json.loads(raw_metadata)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _safe_obs_to_dict(obs) -> dict:
    """
    安全将 observation 对象转换为字典

    Langfuse SDK 4.x 返回的是对象，不是字典
    """
    if isinstance(obs, dict):
        return obs
    if hasattr(obs, 'model_dump'):
        return obs.model_dump()
    if hasattr(obs, '__dict__'):
        return vars(obs)
    return {}


def match_metadata(trace: dict, session_id: str = None, user_id: str = None,
                   user_name: str = None, user_account: str = None) -> bool:
    """
    匹配 metadata 中的字段

    匹配规则（或的关系，任一匹配即可）：
    - session_id: metadata.session_id
    - user_id: metadata.user_id（工号）
    - user_name: metadata.user_name（花名）
    - user_account: metadata.user_account（域账号）
    """
    if not trace:
        return False

    # 安全获取 metadata（处理可能是字符串的情况）
    metadata = _safe_get_metadata(trace)

    # 匹配 session_id（最精确）
    if session_id and metadata.get("session_id") == session_id:
        return True

    # 匹配 user_id（工号）
    if user_id:
        trace_user_id = str(metadata.get("user_id", ""))
        if trace_user_id == str(user_id):
            return True

    # 匹配 user_name（花名）
    if user_name and metadata.get("user_name") == user_name:
        return True

    # 匹配 user_account（域账号）
    if user_account and metadata.get("user_account") == user_account:
        return True

    return False


def extract_machine_name(trace: dict) -> Optional[str]:
    """从 trace 的 metadata 中提取 machine_name"""
    metadata = _safe_get_metadata(trace)
    return metadata.get("machine_name")


def extract_conversation(trace: dict) -> ConversationSession:
    """从 trace 中提取对话内容"""
    session = ConversationSession(
        session_id=trace.get("id", ""),
        meta=_safe_get_metadata(trace)
    )

    # 提取时间信息（SDK 返回的 timestamp 可能是 datetime 对象或字符串）
    timestamp = trace.get("timestamp")
    if timestamp:
        try:
            if isinstance(timestamp, datetime):
                session.start_time = timestamp  # 直接赋值
            elif isinstance(timestamp, str):
                session.start_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    # 从 observations 中提取对话内容
    observations = trace.get("observations", [])
    if not isinstance(observations, list):
        observations = []
    turns = []

    for obs in observations:
        # 安全转换 observation 为字典
        obs_dict = _safe_obs_to_dict(obs)

        obs_type = obs_dict.get("type")
        if obs_type not in ["GENERATION", "SPAN"]:
            continue

        role = "assistant" if obs_type == "GENERATION" else "user"

        # 提取内容
        content = ""
        output = obs_dict.get("output")
        if output:
            if isinstance(output, str):
                content = output
            elif isinstance(output, dict):
                content = output.get("content", str(output))
            elif isinstance(output, list):
                content = "\n".join(str(item) for item in output)

        # 提取工具调用
        tool_calls = []
        obs_metadata = obs_dict.get("metadata", {})
        if isinstance(obs_metadata, dict) and obs_metadata.get("tool_calls"):
            tool_calls = obs_metadata["tool_calls"]

        if content or tool_calls:
            turn = ConversationTurn(
                role=role,
                content=content[:500] if content else "",  # 截断过长内容
                tool_calls=tool_calls[:10] if isinstance(tool_calls, list) else []  # 限制工具调用数量
            )
            turns.append(turn)

    session.turns = turns
    session.total_turns = len(turns)
    return session


def query_conversations(
    session_id: str = None,
    user_id: str = None,
    user_name: str = None,
    user_account: str = None,
    days: float = 0.02,
    limit: int = 20
) -> tuple[list[ConversationSession], set[str]]:
    """
    查询用户对话记录

    Args:
        session_id: 会话 ID（最精确）
        user_id: 用户工号
        user_name: 用户花名
        user_account: 用户域账号
        days: 查询天数（默认 0.02 天 ≈ 30 分钟）
        limit: 返回数量限制

    Returns:
        tuple: (会话列表, machine_name 集合)
    """
    client = get_langfuse_client()

    # 计算时间范围
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    # 查询 traces
    # Langfuse SDK 4.x 使用 client.api.trace.list() 方法
    #
    # 注意：SDK 的 user_id 参数查询的是 Langfuse 内置 user_id 字段，
    # 而 metadata.user_id 是 TeamClaw 自定义存储的字段，需要客户端过滤。
    #
    # 因此我们采用服务端时间过滤 + 客户端 metadata 过滤的策略。
    try:
        # SDK 4.x API: client.api.trace.list()
        # 使用时间范围和 session_id 过滤（session_id 是内置字段，可以服务端过滤）
        query_params = {
            "from_timestamp": start_time,
            "to_timestamp": end_time,
            "limit": min(limit * 5, 100),  # 多取一些用于客户端过滤，最多100条
        }

        # session_id 是 Langfuse 内置字段，可以用服务端过滤
        if session_id:
            query_params["session_id"] = session_id

        traces_response = client.api.trace.list(**query_params)

        # 获取 trace 列表
        traces = traces_response.data if hasattr(traces_response, 'data') else []
        print(f"[DEBUG] 从 Langfuse 获取到 {len(traces)} 条 trace 记录", file=sys.stderr)

    except AttributeError as e:
        # 兼容旧版 SDK (3.x)
        print(f"[DEBUG] 使用旧版 SDK API: {e}", file=sys.stderr)
        try:
            traces = client.get_traces(
                from_timestamp=start_time.isoformat(),
                to_timestamp=end_time.isoformat(),
                limit=limit * 5
            )
            traces = traces.data if hasattr(traces, 'data') else traces
        except Exception as e2:
            print(f"查询 Langfuse 失败 (旧版API): {e2}", file=sys.stderr)
            return [], set()
    except Exception as e:
        print(f"查询 Langfuse 失败: {e}", file=sys.stderr)
        return [], set()

    if not traces:
        print("[DEBUG] 未查询到任何 trace 记录", file=sys.stderr)
        return [], set()

    # 过滤匹配的 traces
    # 注意：user_id、user_name、user_account 存储在 metadata 中，需要客户端过滤
    matched_traces = []
    for trace in traces:
        trace_dict = trace if isinstance(trace, dict) else trace.model_dump() if hasattr(trace, 'model_dump') else {}

        # 调试：打印 metadata 中的用户信息
        metadata = trace_dict.get("metadata", {}) or {}
        if user_id and not matched_traces:  # 只打印前几条用于调试
            print(f"[DEBUG] trace metadata.user_id={metadata.get('user_id')}, 查询 user_id={user_id}", file=sys.stderr)

        if match_metadata(trace_dict, session_id, user_id, user_name, user_account):
            matched_traces.append(trace_dict)

    print(f"[DEBUG] 匹配到 {len(matched_traces)} 条 trace 记录 (过滤条件: session_id={session_id}, user_id={user_id}, user_name={user_name}, user_account={user_account})", file=sys.stderr)

    # 提取会话信息和 machine_name
    sessions = []
    machine_names = set()

    for trace in matched_traces[:limit]:
        session = extract_conversation(trace)
        sessions.append(session)

        # 提取 machine_name
        machine_name = extract_machine_name(trace)
        if machine_name:
            machine_names.add(machine_name)

    return sessions, machine_names


def print_results(sessions: list[ConversationSession], machine_names: set[str]):
    """打印查询结果"""
    print(f"\n{'='*60}")
    print(f"查询到 {len(sessions)} 个会话")
    print(f"{'='*60}\n")

    for session in sessions:
        print(f"Session ID: {session.session_id}")
        print(f"时间范围: {session.start_time} - {session.end_time}")
        print(f"对话轮次: {session.total_turns}")

        # 显示最近 5 轮对话
        print("\n最近对话内容:")
        for turn in session.turns[-5:]:
            role_icon = "🤖" if turn.role == "assistant" else "👤"
            # 安全处理 content 可能为 None 的情况
            content_preview = (turn.content[:200] + "...") if turn.content and len(turn.content) > 200 else (turn.content or "")
            print(f"  {role_icon} [{turn.role}]: {content_preview}")
            # 安全处理 tool_calls
            if turn.tool_calls:
                tool_calls_list = list(turn.tool_calls) if turn.tool_calls else []
                for tc in tool_calls_list[:3]:
                    if isinstance(tc, dict):
                        status = "✓" if tc.get("success", True) else "✗"
                        print(f"    🔧 Tool: {tc.get('tool_name', 'unknown')} -> {status}")
                    else:
                        print(f"    🔧 Tool: {tc}")

        print("-" * 40)

    # 输出 machine_name（用于后续日志查询）
    if machine_names:
        print(f"\n{'='*60}")
        print("发现的 machine_name（用于 Adaptor/OpenClaw 日志查询）:")
        print("="*60)
        for mn in machine_names:
            print(f"  {mn}")

    return machine_names


def main():
    parser = argparse.ArgumentParser(
        description="Langfuse 对话记录查询工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 按工号查询（近30分钟）
    python langfuse_query.py --user-id "103892"

    # 按花名查询
    python langfuse_query.py --user-name "楚生" --days 1

    # 按 session_id 查询（最精确）
    python langfuse_query.py --session-id "session_xxx" --days 7

    # 按域账号查询
    python langfuse_query.py --user-account "qianlingke.qlk"
        """
    )

    parser.add_argument("--session-id", help="会话 ID（最精确）")
    parser.add_argument("--user-id", help="用户工号")
    parser.add_argument("--user-name", help="用户花名")
    parser.add_argument("--user-account", help="用户域账号")
    parser.add_argument("--days", type=float, default=0.02, help="查询天数（默认 0.02 ≈ 30分钟）")
    parser.add_argument("--limit", type=int, default=20, help="返回数量限制")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()

    # 至少需要一个查询条件
    if not any([args.session_id, args.user_id, args.user_name, args.user_account]):
        parser.error("至少需要一个查询条件：--session-id, --user-id, --user-name 或 --user-account")

    try:
        sessions, machine_names = query_conversations(
            session_id=args.session_id,
            user_id=args.user_id,
            user_name=args.user_name,
            user_account=args.user_account,
            days=args.days,
            limit=args.limit
        )

        if args.json:
            # JSON 输出
            result = {
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "start_time": s.start_time.isoformat() if s.start_time else None,
                        "end_time": s.end_time.isoformat() if s.end_time else None,
                        "total_turns": s.total_turns,
                        "machine_name": s.meta.get("machine_name")
                    }
                    for s in sessions
                ],
                "machine_names": list(machine_names)
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_results(sessions, machine_names)

    except ValueError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"查询失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()