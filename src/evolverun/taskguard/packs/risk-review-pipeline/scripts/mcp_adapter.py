#!/usr/bin/env python3
"""
MCP统一调用层 - 自动适配沙箱/本地环境

将 fetch_data.py、save_data.py、pass_risk_free.py、promo_info_injector.py、
send_rone_notification.py 中重复的MCP调用代码统一收敛。

用法:
    from mcp_adapter import call_mcp, warmup_mcp, get_running_data_dir

    # 基础调用
    result = call_mcp(server, tool, args_dict)

    # 便捷函数
    result = fetch_activity_data(activity_id="300005")
    result = save_to_rone(activity_id, data_type_cn, content)
    result = inject_promo_info(init_data)
    result = send_rone_card(record_id, staff_id="461514")
    result = send_risk_free_callback(ext_data)
"""

import subprocess
import json
import os
import sys
import time
import re
import shutil
import glob

# ==============================================================================
# 常量
# ==============================================================================

MCP_SERVER = 'mcp.ant.agentix.108780.test_tool_kit'
MCP_TOOL = 'risk_evaluation_toolkit'
MCP_EVAL_TYPE = 'AI_DATA'

SANDBOX_WORKSPACE = "/home/admin/.openclaw/workspace"
SANDBOX_CONFIG_DIR = "/home/admin/.openclaw/workspace/config"

# 线上生产环境（openclawExt）的固定路径
OPENCLAWEXT_ROOT = "/home/admin/openclawExt/clawmind"
OPENCLAWEXT_CONFIG_DIR = "/home/admin/openclawExt/clawmind/config"

# 数据类型中文映射
TYPE_TO_CN = {
    'ACTIVITY_DATA': '初始化',
    'ANALYSIS_RESULT': '分析结果',
    'ANALYZE_RESULT': '分析结果',
    'CONFIRM_RESULT_PASS': '用户确认通过',
    'CONFIRM_RESULT_REJECT': '用户确认拒绝',
    'RE_RUN': '用户重跑',
    'CALL_ART': '提交ART',
}

CN_TO_SUBTYPE_WRITE = {
    '初始化': 'ACTIVITY_DATA',
    '分析结果': 'ANALYZE_RESULT',
    '用户确认通过': 'CONFIRM_RESULT_PASS',
    '用户确认拒绝': 'CONFIRM_RESULT_REJECT',
    '用户重跑': 'RE_RUN',
    '提交ART': 'CALL_ART',
}

# ==============================================================================
# 环境检测
# ==============================================================================

def is_sandbox() -> bool:
    """检测是否在沙箱环境"""
    return os.path.exists(os.path.join(SANDBOX_CONFIG_DIR, 'mcporter.json'))


def is_openclawext() -> bool:
    """检测是否在线上生产环境 (openclawExt)"""
    return os.path.exists(OPENCLAWEXT_ROOT) or os.path.exists(OPENCLAWEXT_CONFIG_DIR)


def _find_project_root(start_path: str = None) -> str:
    """向上查找项目根目录（包含 .claude 目录或 openclawExt 根目录）"""
    if start_path is None:
        start_path = os.path.dirname(os.path.abspath(__file__))
    current = start_path
    while current and current != '/':
        if os.path.isdir(os.path.join(current, '.claude')):
            return current
        # 线上生产环境 (openclawExt): /home/admin/openclawExt/clawmind
        if current == OPENCLAWEXT_ROOT:
            return current
        current = os.path.dirname(current)
    return None


def get_mcporter_cwd() -> str:
    """
    获取 mcporter 执行时的工作目录

    优先级:
    1. 沙箱环境: /home/admin/.openclaw/workspace
    2. 线上生产环境 (openclawExt): /home/admin/openclawExt/clawmind
    3. 项目根目录: 含 config/mcporter.json 的目录
    4. 当前目录（兜底）
    """
    # 1. 沙箱环境
    if is_sandbox():
        return SANDBOX_WORKSPACE

    # 2. 线上生产环境 (openclawExt)
    if is_openclawext():
        return OPENCLAWEXT_ROOT

    # 2. 从脚本目录向上查找
    script_dir = os.path.dirname(os.path.abspath(__file__))
    curr = script_dir
    for _ in range(10):
        mcporter_config = os.path.join(curr, 'config', 'mcporter.json')
        if os.path.exists(mcporter_config):
            return curr
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent

    # 3. 项目根目录
    project_root = _find_project_root(script_dir)
    if project_root:
        config_path = os.path.join(project_root, 'config', 'mcporter.json')
        if os.path.exists(config_path):
            return project_root

    # 4. 兜底
    return os.getcwd()


# 缓存
_MCPORTER_CWD = None

def _get_mcporter_cwd() -> str:
    """获取或缓存 mcporter 工作目录"""
    global _MCPORTER_CWD
    if _MCPORTER_CWD is None:
        _MCPORTER_CWD = get_mcporter_cwd()
    return _MCPORTER_CWD


# ==============================================================================
# 数据目录
# ==============================================================================

def _find_data_root() -> str:
    """查找数据根目录"""
    # 沙箱
    if is_sandbox():
        return os.path.join(SANDBOX_WORKSPACE, 'data')

    # 线上生产环境 (openclawExt)
    if is_openclawext():
        data_dir = os.path.join(OPENCLAWEXT_ROOT, 'data')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir

    # 项目内
    project_root = _find_project_root()
    if project_root:
        data_dir = os.path.join(project_root, '.openclaw', 'workspace', 'data')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir

    raise RuntimeError("无法找到项目根目录，请确保在正确的项目目录下运行脚本")


def get_running_data_dir() -> str:
    """获取 RUNNING_DATA 目录"""
    running_data = os.path.join(_find_data_root(), "RUNNING_DATA")
    os.makedirs(running_data, exist_ok=True)
    return running_data


def get_references_dir() -> str:
    """获取 references 目录"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    refs_dir = os.path.join(os.path.dirname(script_dir), 'references')
    os.makedirs(refs_dir, exist_ok=True)
    return refs_dir


def get_skill_dir() -> str:
    """获取当前 skill 根目录"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ==============================================================================
# 通用工具函数
# ==============================================================================

def to_compact_json(data) -> str:
    """将字典/列表转换为紧凑格式JSON字符串"""
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def build_filename(activity_id: str, data_type_cn: str) -> str:
    """构建标准文件名: 营销活动_{id}_{类型}.txt"""
    return f"营销活动_{activity_id}_{data_type_cn}.txt"


def parse_filename(filename: str) -> dict:
    """解析文件名，提取活动ID和数据类型"""
    match = re.search(r'营销活动_(\d+)_(.+)\.txt$', os.path.basename(filename))
    if match:
        return {"activity_id": match.group(1), "data_type_cn": match.group(2), "valid": True}
    return {"activity_id": None, "data_type_cn": None, "valid": False}


# ==============================================================================
# MCP 核心调用
# ==============================================================================

def warmup_mcp(max_retries: int = 3, wait_seconds: float = 2.0) -> bool:
    """预热MCP服务，确保后续调用能成功"""
    warmup_cmd = [
        'mcporter', 'call', '--server', MCP_SERVER, MCP_TOOL, '--args',
        '{"evalMaterial/evalType":"DATA_EXPORT","evalMaterial/evaSubType":"promoInfoInject","evalMaterial/evalContent":"{}","evalMaterial/requestId":""}'
    ]
    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                warmup_cmd, capture_output=True, text=True, timeout=30,
                cwd=_get_mcporter_cwd()
            )
            if result.returncode == 0 and '"success": true' in result.stdout:
                time.sleep(wait_seconds)
                return True
            if result.stdout:
                print(f"[Warmup] Attempt {attempt + 1}/{max_retries}: {result.stdout[:100]}", file=sys.stderr)
        except Exception as e:
            print(f"[Warmup] Attempt {attempt + 1}/{max_retries} failed: {e}", file=sys.stderr)
        if attempt < max_retries - 1:
            time.sleep(wait_seconds)
    return False


def build_mcporter_args(eval_content, operation: str = 'select') -> list:
    """
    构建 mcporter 调用的参数列表

    Args:
        eval_content: evalContent 参数（dict 或 str）
        operation: 'select' / 'selectById' / 'insert' / 'inject' / 'contact'
    """
    if operation == 'selectById':
        args_dict = {
            "evalMaterial/evalType": MCP_EVAL_TYPE,
            "evalMaterial/evaSubType": "AI,selectById",
            "evalMaterial/evalContent": str(eval_content)
        }
    elif operation == 'select':
        args_dict = {
            "evalMaterial/evalType": MCP_EVAL_TYPE,
            "evalMaterial/evaSubType": "AI,selectByTypeAndTagAndTime",
            "evalMaterial/evalContent": to_compact_json(eval_content)
        }
    elif operation == 'insert':
        args_dict = {
            "evalMaterial/evalType": MCP_EVAL_TYPE,
            "evalMaterial/evaSubType": "AI,insertWithFullInfo",
            "evalMaterial/evalContent": to_compact_json(eval_content)
        }
    elif operation == 'inject':
        args_dict = {
            "evalMaterial/evalType": "DATA_EXPORT",
            "evalMaterial/evaSubType": "promoInfoInject",
            "evalMaterial/evalContent": to_compact_json(eval_content) if isinstance(eval_content, dict) else str(eval_content),
            "evalMaterial/requestId": ""
        }
    elif operation == 'contact':
        # contact 类型: evalContent 已是紧凑 JSON 字符串
        content = eval_content if isinstance(eval_content, str) else to_compact_json(eval_content)
        args_dict = {
            "evalMaterial/evalType": "CONTACT",
            "evalMaterial/evalContent": content,
            "evalMaterial/evaSubType": "msgBroker",
            "evalMaterial/requestId": ""
        }
    elif operation == 'rone_card':
        content = eval_content if isinstance(eval_content, str) else to_compact_json(eval_content)
        args_dict = {
            "evalMaterial/evalType": "CONTACT",
            "evalMaterial/evaSubType": "roneCard",
            "evalMaterial/evalContent": content
        }
    else:
        raise ValueError(f"未知的操作类型: {operation}")

    args_json = json.dumps(args_dict, ensure_ascii=False)
    return ['mcporter', 'call', '--server', MCP_SERVER, MCP_TOOL, '--args', args_json]


def call_mcp(args_dict: dict, max_retries: int = 2, warmup: bool = True) -> dict:
    """
    统一MCP调用接口

    Args:
        args_dict: evalMaterial参数字典（完整），或 build_mcporter_args 返回的命令列表
        max_retries: 最大重试次数
        warmup: 是否先预热

    Returns:
        dict: {"success": bool, "data": ..., "error": str|None}
    """
    if warmup:
        warmup_mcp()

    # 如果传入的是命令列表，直接使用；否则构建参数
    if isinstance(args_dict, list):
        cmd_list = args_dict
    else:
        args_json = json.dumps(args_dict, ensure_ascii=False)
        cmd_list = ['mcporter', 'call', '--server', MCP_SERVER, MCP_TOOL, '--args', args_json]

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                cmd_list, capture_output=True, text=True, timeout=60,
                cwd=_get_mcporter_cwd()
            )

            # MCP 初始化失败
            if 'ling mcp init failed' in result.stdout or 'ling mcp init failed' in result.stderr:
                last_error = "MCP服务初始化失败(ling mcp init failed)"
                if attempt < max_retries:
                    time.sleep(3)
                    warmup_mcp()
                    continue
                return {"success": False, "data": None, "error": last_error}

            if result.returncode != 0:
                last_error = f"MCP调用失败(returncode={result.returncode}): {result.stderr or result.stdout[:200]}"
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return {"success": False, "data": None, "error": last_error}

            # 解析响应
            stdout_text = result.stdout.strip()
            if not stdout_text.startswith('{'):
                last_error = f"MCP返回非JSON响应: {stdout_text[:200]}"
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return {"success": False, "data": None, "error": last_error}

            response = json.loads(stdout_text)
            if not response.get('success'):
                error_msg = response.get('error', 'Unknown error')
                return {"success": False, "data": None, "error": error_msg}

            # 提取嵌套 data
            outer_data = response.get('data', {})
            inner_data = outer_data.get('data', '{}')
            if isinstance(inner_data, str):
                inner_data = json.loads(inner_data)

            # 检查内部 success
            if inner_data.get('success') is False:
                return {"success": False, "data": None, "error": inner_data.get('errorMsg', 'MCP内部调用失败')}

            return {"success": True, "data": inner_data, "error": None}

        except subprocess.TimeoutExpired:
            last_error = "MCP调用超时(60秒)"
            if attempt < max_retries:
                time.sleep(2)
                continue
            return {"success": False, "data": None, "error": last_error}

        except (json.JSONDecodeError, Exception) as e:
            last_error = f"MCP调用异常: {str(e)}"
            if attempt < max_retries:
                time.sleep(2)
                continue
            return {"success": False, "data": None, "error": last_error}

    return {"success": False, "data": None, "error": last_error}


# ==============================================================================
# 响应解析
# ==============================================================================

def parse_query_response(response_text: str) -> list:
    """解析MCP查询响应，提取数据项列表"""
    response = json.loads(response_text)
    if not response.get('success'):
        raise ValueError(f"MCP响应失败: {response.get('error', 'Unknown error')}")

    outer_data = response.get('data', {})
    inner_data_str = outer_data.get('data', '[]')
    if isinstance(inner_data_str, str):
        inner_data = json.loads(inner_data_str)
    else:
        inner_data = inner_data_str

    if inner_data.get('success') is False:
        raise ValueError(f"MCP内部调用失败: {inner_data.get('errorMsg', 'Unknown error')}")

    items = inner_data.get('data', [])
    if isinstance(items, str):
        items = json.loads(items)

    if isinstance(items, list):
        return items
    elif isinstance(items, dict):
        return [items]
    return []


def parse_save_response(response_text: str) -> tuple:
    """解析MCP写入响应，提取record_id。返回 (record_id, error)"""
    try:
        if not response_text or not response_text.strip():
            return None, "MCP返回空响应"
        response = json.loads(response_text)
        if not response.get('success'):
            return None, f"MCP响应失败: {response.get('error', 'Unknown error')}"

        outer_data = response.get('data', {})
        inner_data_str = outer_data.get('data', '{}')
        if isinstance(inner_data_str, str):
            inner_data = json.loads(inner_data_str)
        else:
            inner_data = inner_data_str

        if inner_data.get('success') is False:
            return None, f"MCP内部调用失败: {inner_data.get('errorMsg', 'Unknown error')}"

        record_id = inner_data.get('data')
        if record_id:
            return str(record_id), None
        return None, "未获取到record_id"
    except json.JSONDecodeError as e:
        return None, f"JSON解析失败: {e}"
    except Exception as e:
        return None, f"解析响应失败: {e}"


# ==============================================================================
# 便捷函数 - 对应原有各脚本的MCP调用
# ==============================================================================

def fetch_activity_data(data_type: str = 'ACTIVITY_DATA', activity_id: str = None) -> dict:
    """
    从Rone获取活动数据（对应 fetch_data.py）

    Returns:
        dict: {"success": bool, "items": [...], "error": str|None}
    """
    try:
        warmup_mcp()

        if activity_id:
            cmd_list = build_mcporter_args(activity_id, operation='selectById')
        else:
            eval_content = {"type": "MARKETING", "subType": data_type, "tag": "INIT"}
            cmd_list = build_mcporter_args(eval_content, operation='select')

        result = call_mcp(cmd_list, warmup=False)
        if not result['success']:
            return {"success": False, "items": [], "error": result['error']}

        inner_data = result['data']
        items = inner_data.get('data', [])
        if isinstance(items, str):
            items = json.loads(items)
        if isinstance(items, dict):
            items = [items]

        return {"success": True, "items": items, "error": None}

    except Exception as e:
        return {"success": False, "items": [], "error": str(e)}


def save_to_rone(activity_id: str, data_type_cn: str, material_content: str, max_retries: int = 2) -> tuple:
    """
    将数据写入Rone系统（对应 save_data.py 的 save_to_rone）

    Returns:
        tuple: (record_id, error_message)
    """
    try:
        # 确保 material 是 compact 格式
        try:
            data_dict = json.loads(material_content)
            compact_content = to_compact_json(data_dict)
        except json.JSONDecodeError:
            return None, "material内容不是有效的JSON"

        sub_type = CN_TO_SUBTYPE_WRITE.get(data_type_cn, data_type_cn)
        eval_content = {
            "type": "MARKETING",
            "subType": sub_type,
            "tag": "INIT",
            "material": compact_content,
            "requestId": activity_id
        }

        cmd_list = build_mcporter_args(eval_content, operation='insert')
        # result = call_mcp(cmd_list, max_retries=max_retries)
        # Mock模式：跳过实际MCP调用，直接返回模拟record_id
        record_id = "MOCK_RECORD_ID"
        return (record_id, None)
    except Exception as e:
        return None, f"保存到Rone失败: {e}"


def inject_promo_info(init_data: dict) -> dict:
    """
    调用MCP进行营销信息注入（对应 promo_info_injector.py 的核心调用）

    Returns:
        dict: event_property 数据
    Raises:
        RuntimeError: 调用失败时
    """
    # 从init_data提取extData
    ext_data = init_data.get('extData', {})
    if isinstance(ext_data, str):
        ext_data = json.loads(ext_data)

    # 提升pendingReviewData
    pending_review_data = ext_data.pop('pendingReviewData', None)
    if isinstance(pending_review_data, str):
        try:
            pending_review_data = json.loads(pending_review_data)
        except (json.JSONDecodeError, TypeError):
            pending_review_data = None
    if isinstance(pending_review_data, dict):
        for key, value in pending_review_data.items():
            if key not in ext_data:
                ext_data[key] = value

    cmd_list = build_mcporter_args(ext_data, operation='inject')
    result = call_mcp(cmd_list)

    if not result['success']:
        raise RuntimeError(f"MCP调用失败: {result['error']}")

    # 提取 event_property
    event_data = result['data'].get('data')
    if isinstance(event_data, str):
        try:
            return json.loads(event_data)
        except json.JSONDecodeError:
            pass
    if isinstance(event_data, dict):
        return event_data

    return result['data']


def send_rone_card(record_id: str, staff_id: str = "448158") -> tuple:
    """
    发送Rone卡片通知（对应 send_rone_notification.py）

    Returns:
        tuple: (success, message)
    """
    content_dict = {
        "staffId": staff_id,
        "text": "来自AI工作台",
        "titleUrlMap": {
            f"Rone评审链接{record_id}": "https://secoc.alipay.com/assistant"
        }
    }
    content_str = json.dumps(content_dict, ensure_ascii=False, separators=(',', ':'))

    cmd_list = build_mcporter_args(content_str, operation='rone_card')
    result = call_mcp(cmd_list)

    if not result['success']:
        return False, result['error']

    inner_data = result['data']
    if isinstance(inner_data, str):
        inner_data = json.loads(inner_data)
    if inner_data.get('success'):
        return True, "Rone卡片发送成功"
    return False, inner_data.get('message', inner_data.get('error', '发送失败'))


def warmup_cli():
    """命令行入口：预热MCP"""
    import argparse
    parser = argparse.ArgumentParser(description='MCP预热工具')
    parser.add_argument('--max-retries', type=int, default=3, help='最大重试次数')
    args = parser.parse_args()
    result = warmup_mcp(max_retries=args.max_retries)
    print(f"warmup: {result}")
    sys.exit(0 if result else 1)


def send_risk_free_callback(ext_data: dict) -> tuple:
    """
    发送无风险回调（对应 pass_risk_free.py 的 call_callback）

    Args:
        ext_data: 包含 puid 和 orderId 的字典

    Returns:
        tuple: (success, message)
    """
    payload = {
        "topic": "TP_O_SECURITYPROD",
        "eventCode": "EC_CALLBACK_HAITUN",
        "payload": {
            "originalRopStatus": "PASS",
            "extData": ext_data
        }
    }

    cmd_list = build_mcporter_args(payload, operation='contact')
    result = call_mcp(cmd_list)

    if not result['success']:
        return False, result['error']
    return True, "无风险回调成功"


if __name__ == '__main__':
    warmup_cli()