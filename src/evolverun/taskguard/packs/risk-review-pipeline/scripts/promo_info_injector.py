#!/usr/bin/env python3
"""
营销信息注入脚本 - 使用 mcp_adapter

读取初始化文件，调用MCP promoInfoInject，保存event_property。

用法:
    python3 promo_info_injector.py --input_file "营销活动_300005_初始化.txt"
    python3 promo_info_injector.py --activity-id 300005
"""

import json
import os
import sys
import argparse
import time

from mcp_adapter import (
    inject_promo_info, warmup_mcp, get_running_data_dir,
    get_references_dir, build_filename, parse_filename,
    is_sandbox
)

RUNNING_DATA_DIR = get_running_data_dir()
REFERENCES_DIR = get_references_dir()

# 数据完整性检测
REQUIRED_EP_FIELDS = ['planBasicInfo', 'prizeBasicInfoAll']
RECOMMENDED_EP_FIELDS = ['campBasicInfo.countControlConfigDTOs', 'campBasicInfo.extProperties', 'prizeVoucherBasicInfo']

MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 2
RETRY_BACKOFF_FACTOR = 2
RETRYABLE_ERRORS = ['400', '429', '500', '502', '503', '504', 'timeout', 'connection']


def check_data_completeness(event_property):
    """检测event_property数据完整性"""
    missing_required = [f for f in REQUIRED_EP_FIELDS if f not in event_property or not event_property.get(f)]
    missing_recommended = []
    for field_path in RECOMMENDED_EP_FIELDS:
        parts = field_path.split('.')
        value = event_property
        found = True
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                found = False
                break
        if not found or (isinstance(value, (list, dict)) and len(value) == 0):
            missing_recommended.append(field_path)

    is_complete = len(missing_required) == 0
    warning = ""
    if missing_required:
        warning = f"数据不完整，缺失必需字段: {', '.join(missing_required)}"
    elif missing_recommended:
        warning = f"数据完整但缺失建议字段: {', '.join(missing_recommended)}"

    return {"is_complete": is_complete, "missing_required": missing_required,
            "missing_recommended": missing_recommended, "warning": warning}


def find_input_file(input_file, activity_id=None):
    """查找输入文件"""
    if os.path.isabs(input_file) and os.path.exists(input_file):
        return input_file

    # 标准位置
    std_path = os.path.join(RUNNING_DATA_DIR, input_file)
    if os.path.exists(std_path):
        return std_path

    # 按活动ID查找
    if activity_id:
        id_path = os.path.join(RUNNING_DATA_DIR, f"营销活动_{activity_id}_初始化.txt")
        if os.path.exists(id_path):
            return id_path

    raise FileNotFoundError(f"找不到文件: {input_file}")


def main():
    parser = argparse.ArgumentParser(description='营销信息注入')
    parser.add_argument('--input_file', '-i', type=str, help='初始化文件名或路径')
    parser.add_argument('--activity-id', '-a', type=str, help='活动ID')
    parser.add_argument('--output', '-o', type=str, help='event_property输出路径')
    args = parser.parse_args()

    # 注意：不再从 stdin 读取。workflow cli-script executor 的 stdin
    # 继承 gateway daemon 进程，永远不会发 EOF，sys.stdin.read() 会永久阻塞。
    # 改用 --input-file 或 --activity-id 参数传入。

    if not args.input_file and not args.activity_id:
        print(json.dumps({"success": False, "error": "需要提供 --input_file 或 --activity-id"}, ensure_ascii=False))
        sys.exit(1)

    # 确定活动ID
    activity_id = args.activity_id
    if not activity_id and args.input_file:
        parsed = parse_filename(args.input_file)
        if parsed['valid']:
            activity_id = parsed['activity_id']

    try:
        # 查找输入文件
        if args.input_file:
            input_path = find_input_file(args.input_file, activity_id)
        elif activity_id:
            input_path = find_input_file(f"营销活动_{activity_id}_初始化.txt", activity_id)
        else:
            raise FileNotFoundError("无法确定输入文件")

        print(f"[INFO] 加载初始化文件: {input_path}", file=sys.stderr)
        with open(input_path, 'r', encoding='utf-8') as f:
            init_data = json.load(f)

        # 调用MCP（带重试）
        event_property = None
        last_error = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                event_property = inject_promo_info(init_data)
                completeness = check_data_completeness(event_property)

                if not completeness["is_complete"]:
                    if attempt < MAX_RETRIES:
                        delay = INITIAL_RETRY_DELAY * (RETRY_BACKOFF_FACTOR ** attempt)
                        print(f"[WARN] 数据不完整 (尝试 {attempt + 1}/{MAX_RETRIES + 1}): {completeness['warning']}", file=sys.stderr)
                        time.sleep(delay)
                        last_error = completeness["warning"]
                        continue
                    else:
                        print(f"[WARN] 达到最大重试次数，数据仍不完整: {completeness['warning']}", file=sys.stderr)
                elif completeness["warning"]:
                    print(f"[WARN] {completeness['warning']}", file=sys.stderr)

                if attempt > 0:
                    print(f"[INFO] 重试成功 (第 {attempt + 1} 次尝试)", file=sys.stderr)
                break

            except RuntimeError as e:
                last_error = str(e)
                if attempt < MAX_RETRIES:
                    delay = INITIAL_RETRY_DELAY * (RETRY_BACKOFF_FACTOR ** attempt)
                    print(f"[WARN] 调用失败 (尝试 {attempt + 1}/{MAX_RETRIES + 1}): {last_error}", file=sys.stderr)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"{last_error}，已达到最大重试次数")

        if event_property is None:
            raise RuntimeError(f"获取活动数据失败: {last_error}")

        # 保存event_property
        os.makedirs(REFERENCES_DIR, exist_ok=True)
        if args.output:
            output_path = args.output
        elif activity_id:
            output_path = os.path.join(REFERENCES_DIR, f"event_property_{activity_id}.json")
        else:
            output_path = os.path.join(REFERENCES_DIR, "event_property.json")

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(event_property, f, ensure_ascii=False, indent=2)

        completeness = check_data_completeness(event_property)
        result = {
            "success": True,
            "event_property_path": output_path,
            "activity_id": activity_id,
            "data_complete": completeness["is_complete"],
            "missing_fields": completeness["missing_required"] + completeness["missing_recommended"],
            "message": f"{os.path.basename(output_path)} 已保存到: {output_path}"
        }
        if not completeness["is_complete"]:
            result["warning"] = f"数据不完整: {completeness['warning']}"
        print(json.dumps(result, ensure_ascii=False, indent=2))

    except (FileNotFoundError, RuntimeError) as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"执行失败: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()