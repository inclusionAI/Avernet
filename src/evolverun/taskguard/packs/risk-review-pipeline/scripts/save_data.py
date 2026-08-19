#!/usr/bin/env python3
"""
营销活动数据保存脚本 - 使用 mcp_adapter

读取本地分析结果文件，写入Rone系统。

用法:
    python3 save_data.py --file "营销活动_300005_分析结果.txt"
    DATA_FILE="营销活动_300005_初始化.txt" python3 save_data.py

输出:
    {"success": true, "record_id": "700014", "activity_id": "300005"}
"""

import json
import os
import sys

from mcp_adapter import (
    save_to_rone, get_running_data_dir, get_references_dir,
    build_filename, parse_filename, CN_TO_SUBTYPE_WRITE
)

RUNNING_DATA_DIR = get_running_data_dir()
REFERENCES_DIR = get_references_dir()

# 必需字段（评审完整性检查，对齐「数金一页纸内容」文档）
REQUIRED_FIELDS_WITH_ALIASES = [
    (['活动名称'], '活动名称'),
    (['活动CP号', 'campCode'], '活动CP号'),
    (['活动类型'], '活动类型'),
    (['获取限制'], '获取限制'),
    (['是否限制实名'], '是否限制实名'),
    (['是否限制四同'], '是否限制四同'),
    (['活动配置是否合理'], '活动配置是否合理'),
    (['是否有风险', '风险情况'], '是否有风险/风险情况'),
    (['风险判断原因', '风险判定依据'], '风险判断原因/风险判定依据'),
]


def read_result_file(result_file, activity_id=None):
    """读取分析结果文件，支持回退查找"""
    # 标准位置
    result_path = os.path.join(RUNNING_DATA_DIR, result_file)
    if os.path.exists(result_path):
        try:
            with open(result_path, 'r', encoding='utf-8') as f:
                content = f.read()
                json.loads(content)
                return content, None, result_path
        except (json.JSONDecodeError, Exception) as e:
            return None, str(e), None

    # 回退: references 目录
    if activity_id:
        fallback_path = os.path.join(REFERENCES_DIR, f"final_res_{activity_id}.json")
        if os.path.exists(fallback_path):
            try:
                with open(fallback_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    json.loads(content)
                # 同步到标准位置
                try:
                    os.makedirs(os.path.dirname(result_path), exist_ok=True)
                    with open(result_path, 'w', encoding='utf-8') as f:
                        json.dump(json.loads(content), f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return content, None, fallback_path
            except Exception as e:
                return None, str(e), None

    return None, f"结果文件不存在: {result_file}", None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='保存评审结果到Rone')
    parser.add_argument('--activity-id', '-a', type=str, help='活动ID，自动查找分析结果文件')
    parser.add_argument('--file', '-f', type=str, help='直接指定结果文件名')
    args = parser.parse_args()

    activity_id = args.activity_id
    data_file = args.file

    # 回退: 环境变量或stdin
    if not data_file:
        if os.environ.get('DATA_FILE'):
            data_file = os.environ.get('DATA_FILE')
        # 注意：不再从 stdin 读取。workflow cli-script executor 的 stdin
        # 继承 gateway daemon 进程，永远不会发 EOF，sys.stdin.read() 会永久阻塞。
        # 改用 ARG_ACTIVITY_ID 环境变量或 --activity-id 参数传入。

    # 通过activity_id构造文件名
    if not data_file and activity_id:
        data_file = build_filename(activity_id, '分析结果')

    if not data_file:
        print(json.dumps({"success": False, "error": "需要提供 --activity-id 或 --file 参数"}, ensure_ascii=False))
        sys.exit(1)

    if not activity_id:
        parsed = parse_filename(data_file)
        if parsed['valid']:
            activity_id = parsed['activity_id']
        else:
            print(json.dumps({"success": False, "error": f"无法从文件名解析活动ID: {data_file}"}, ensure_ascii=False))
            sys.exit(1)
    source_data_type_cn = '分析结果'

    # 确定目标数据类型
    target_data_type_cn = os.environ.get('DATA_TYPE_CN', '分析结果')

    # 确定结果文件名
    if target_data_type_cn == '分析结果':
        result_file = build_filename(activity_id, '分析结果')
    else:
        result_file = build_filename(activity_id, target_data_type_cn)

    # 读取结果文件
    content, error, source_path = read_result_file(result_file, activity_id)
    if error:
        print(json.dumps({"success": False, "error": error, "activity_id": activity_id}, ensure_ascii=False))
        sys.exit(1)

    # 检查风险等级和完整性
    risk_level = "低风险"
    activity_name = ""
    missing_fields = []
    is_complete = True

    try:
        result_data = json.loads(content)
        activity_name = result_data.get('活动名称', result_data.get('campName', ''))

        for aliases, display_name in REQUIRED_FIELDS_WITH_ALIASES:
            found = False
            for alias in aliases:
                value = result_data.get(alias)
                if value is not None and value != '' and not (isinstance(value, list) and len(value) == 0):
                    found = True
                    break
            if not found:
                missing_fields.append(display_name)

        if missing_fields:
            is_complete = False
            print(f"[WARNING] 评审结果不完整，缺少字段: {missing_fields}", file=sys.stderr)

        has_risk = result_data.get('是否有风险') or result_data.get('风险情况')
        if has_risk is None:
            # 字段缺失时，根据配置校验结果推断风险等级
            config_valid = result_data.get('活动配置是否合理', '未知')
            if config_valid == '有配置风险' or config_valid == '否':
                has_risk = '是'
                print(f"[WARNING] '是否有风险'字段缺失，根据配置校验结果推断为高风险", file=sys.stderr)
            else:
                has_risk = '否'
                print(f"[WARNING] '是否有风险'字段缺失，默认为低风险", file=sys.stderr)

        if has_risk in ('是', True, 'true', 'TRUE', '高风险', '中风险'):
            risk_level = "高风险"
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"[ERROR] 解析结果文件失败: {e}", file=sys.stderr)
        is_complete = False

    # 写入Rone
    record_id, save_error = save_to_rone(activity_id, target_data_type_cn, content)
    if save_error:
        print(json.dumps({
            "success": False, "error": save_error,
            "activity_id": activity_id, "target_type": target_data_type_cn
        }, ensure_ascii=False))
        sys.exit(1)

    result = {
        "success": True, "record_id": record_id, "activity_id": activity_id,
        "data_type_cn": target_data_type_cn, "source_file": data_file,
        "result_file": result_file, "risk_level": risk_level,
        "activity_name": activity_name, "is_complete": is_complete
    }
    if not is_complete:
        result["missing_fields"] = missing_fields
        result["warning"] = "评审结果不完整，建议调用恢复流程补全缺失字段"

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == '__main__':
    main()