#!/usr/bin/env python3
"""
无风险通过回调脚本 - 使用 mcp_adapter

当评审结果为"无风险"时，发送回调通知。

用法:
    python3 pass_risk_free.py --activity-id 300005
    DATA_FILE="营销活动_300005_初始化.txt" python3 pass_risk_free.py

输出:
    {"success": true, "activity_id": "300005", "skipped": false, "message": "无风险回调成功"}
"""

import json
import os
import sys

from mcp_adapter import (
    send_risk_free_callback, get_running_data_dir,
    build_filename, parse_filename
)

RUNNING_DATA_DIR = get_running_data_dir()


def read_json_file(file_path):
    """读取并解析 JSON 文件"""
    if not os.path.exists(file_path):
        return None, f"文件不存在: {file_path}"
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f), None
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失败: {e}"
    except Exception as e:
        return None, f"读取文件失败: {e}"


def get_risk_status(result_data):
    """从分析结果中获取风险状态"""
    has_risk = result_data.get('是否有风险')
    if has_risk:
        if has_risk in ('是', True, 'true', 'TRUE', '高风险', '中风险'):
            return "有风险"
        elif has_risk in ('否', False, 'false', 'FALSE', '无风险', '低风险'):
            return "无风险"

    risk_status = result_data.get('风险情况')
    if risk_status:
        if risk_status in ('是', '高风险', '中风险'):
            return "有风险"
        elif risk_status in ('否', '无风险', '低风险'):
            return "无风险"

    return "有风险"


def extract_ext_data(init_data):
    """从初始化数据提取 extData.extData"""
    ext_data_str = init_data.get('extData')
    if not ext_data_str:
        return None, "初始化数据缺少 extData 字段"

    try:
        ext_data_outer = json.loads(ext_data_str) if isinstance(ext_data_str, str) else ext_data_str
        ext_data_inner = ext_data_outer.get('extData')
        if not ext_data_inner:
            return None, "extData 中缺少嵌套的 extData 字段"

        if 'puid' not in ext_data_inner:
            return None, "extData 中缺少 puid 字段"
        if 'orderId' not in ext_data_inner:
            return None, "extData 中缺少 orderId 字段"

        return ext_data_inner, None
    except json.JSONDecodeError as e:
        return None, f"extData 字段 JSON 解析失败: {e}"


def main():
    data_file = None
    activity_id = None

    import argparse
    parser = argparse.ArgumentParser(description='无风险通过回调')
    parser.add_argument('--activity-id', '-a', type=str, help='活动ID')
    args = parser.parse_args()

    activity_id = args.activity_id
    if not activity_id:
        if os.environ.get('DATA_FILE'):
            data_file = os.environ.get('DATA_FILE')
        # 注意：不再从 stdin 读取。workflow cli-script executor 的 stdin
        # 继承 gateway daemon 进程，永远不会发 EOF，sys.stdin.read() 会永久阻塞。
        # 改用 ARG_ACTIVITY_ID 环境变量或 --activity-id 参数传入。

        if data_file:
            parsed = parse_filename(data_file)
            if parsed['valid']:
                activity_id = parsed['activity_id']

    if not activity_id:
        print(json.dumps({"success": False, "error": "需要提供 --activity-id 或 DATA_FILE"}, ensure_ascii=False))
        sys.exit(1)

    # 检查分析结果
    result_file = build_filename(activity_id, '分析结果')
    result_path = os.path.join(RUNNING_DATA_DIR, result_file)
    result_data, error = read_json_file(result_path)
    if error:
        print(json.dumps({"success": False, "error": f"无法读取分析结果: {error}", "activity_id": activity_id}, ensure_ascii=False))
        sys.exit(1)

    risk_status = get_risk_status(result_data)
    if risk_status == "有风险":
        print(json.dumps({"success": True, "skipped": True, "activity_id": activity_id, "reason": "活动有风险，跳过回调"}, ensure_ascii=False))
        sys.exit(0)

    # 读取初始化文件，提取extData
    init_file = build_filename(activity_id, '初始化')
    init_path = os.path.join(RUNNING_DATA_DIR, init_file)
    init_data, error = read_json_file(init_path)
    if error:
        print(json.dumps({"success": False, "error": f"无法读取初始化数据: {error}", "activity_id": activity_id}, ensure_ascii=False))
        sys.exit(1)

    ext_data, error = extract_ext_data(init_data)
    if error:
        print(json.dumps({"success": False, "error": f"提取extData失败: {error}", "activity_id": activity_id}, ensure_ascii=False))
        sys.exit(1)

    print(f"[INFO] 提取到 extData: puid={ext_data.get('puid')}, orderId={ext_data.get('orderId')}", file=sys.stderr)

    # 发送回调
    success, msg = send_risk_free_callback(ext_data)
    if not success:
        print(json.dumps({"success": False, "error": msg, "activity_id": activity_id}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps({
        "success": True, "skipped": False, "activity_id": activity_id,
        "message": "无风险回调成功",
        "ext_data": {"puid": ext_data.get('puid'), "orderId": ext_data.get('orderId')}
    }, ensure_ascii=False))
    sys.exit(0)


if __name__ == '__main__':
    main()