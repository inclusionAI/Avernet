#!/usr/bin/env python3
"""
从 bot_info.json 读取 device_id，按 3:3:1 比例批量初始化 kimi/qwen/glm 模型配置
"""

import argparse
import json
import sys

from bot_info_download import load_device_ids
from agentclaw_api_util import exec_shell

# 可选的模型配置
AVAILABLE_MODELS = {
    "kimi": "openclaw config set agents.defaults.model.primary antchat/Kimi-K2.5",
    "qwen": "openclaw config set agents.defaults.model.primary antchat/Qwen3.5-397B-A17B",
    "glm": "openclaw config set agents.defaults.model.primary antchat/GLM-5",
}


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="批量初始化 openclaw 模型配置")
    parser.add_argument(
        "--env", "-e",
        type=str,
        default='pre',
        help="环境标识 (默认：pre)"
    )
    parser.add_argument(
        "--json-file",
        type=str,
        default=None,
        help=f"bot_info.json 文件路径 (默认：bot_info_<env>.json)"
    )
    return parser.parse_args()


def distribute_models(device_ids: list[str], ratio: tuple[int, int, int] = (3, 3, 1)) -> dict[str, list[str]]:
    """
    按照指定比例分配 device_id 到不同模型

    Args:
        device_ids: device_id 列表
        ratio: 分配比例 (kimi:qwen:glm)

    Returns:
        包含各模型 device_id 列表的字典
    """
    total_ratio = sum(ratio)
    total_devices = len(device_ids)

    # 计算每一份的数量
    unit = total_devices // total_ratio
    remainder = total_devices % total_ratio

    result = {}
    start_idx = 0

    model_keys = ["kimi", "qwen", "glm"]

    for i, model_key in enumerate(model_keys):
        count = unit * ratio[i]
        # 将余数分配给最后一项
        if i == len(model_keys) - 1:
            count += remainder

        end_idx = start_idx + count
        result[model_key] = device_ids[start_idx:end_idx]
        start_idx = end_idx

    return result


def main():
    args = parse_args()

    # 加载 device_ids
    device_ids = load_device_ids(args.env)

    if not device_ids:
        print("[ERROR] 没有找到任何 device_id")
        sys.exit(1)

    # 按照 3:3:1 比例分配
    distribution = distribute_models(device_ids, ratio=(3, 3, 1))

    print(f"共加载 {len(device_ids)} 个 device_id")
    print(f"开始批量执行 openclaw 配置更新...")
    print(f"分配比例 kimi:qwen:glm = 3:3:1")
    print(f"  - kimi: {len(distribution['kimi'])} 个")
    print(f"  - qwen: {len(distribution['qwen'])} 个")
    print(f"  - glm:  {len(distribution['glm'])} 个")
    print("=" * 50)

    stats = {}
    for model_key in ["kimi", "qwen", "glm"]:
        stats[model_key] = {"successful": 0, "failed": 0, "failed_list": []}

        model_cmd = AVAILABLE_MODELS[model_key]
        device_list = distribution[model_key]

        print(f"\n>>> 开始处理 {model_key} 模型 ({len(device_list)} 个设备)")
        print(f"命令：{model_cmd}")

        for i, device_id in enumerate(device_list, 1):
            print(f"[{i}/{len(device_list)}] 正在处理：{device_id}")

            success, result = exec_shell(args.env, device_id, model_cmd)

            if success and result.get('success'):
                stats[model_key]["successful"] += 1
                # 解析结果详情
                data = result.get('data', {}).get('results', [])
                if data:
                    res_raw = data[0]
                    # 如果是字符串，尝试解析 JSON
                    if isinstance(res_raw, str):
                        try:
                            res_obj = json.loads(res_raw)
                            stdout = res_obj.get('stdout', '')
                            if stdout:
                                print(f"  -> DONE: {stdout.strip()}")
                            else:
                                print(f"  -> {res_raw}")
                        except json.JSONDecodeError:
                            print(f"  -> {res_raw}")
                    elif isinstance(res_raw, dict):
                        # 可能有一个 'result' 字段
                        res_obj = res_raw.get('result', res_raw)
                        print(f"  -> {res_obj}")
                    else:
                        # 打印完整结果
                        print(f"  -> 结果：{json.dumps(res_raw, ensure_ascii=False)[:200]}")
            else:
                stats[model_key]["failed"] += 1
                stats[model_key]["failed_list"].append({'device_id': device_id, 'error': result})
                print(f"  -> 失败：{result}")

    # 打印汇总
    print("\n" + "=" * 50)
    print("执行汇总:")
    total_successful = sum(s["successful"] for s in stats.values())
    total_failed = sum(s["failed"] for s in stats.values())
    print(f"  总数：{len(device_ids)}")
    print(f"  成功：{total_successful}")
    print(f"  失败：{total_failed}")
    print(f"\n按模型统计:")
    for model_key in ["kimi", "qwen", "glm"]:
        print(f"  {model_key}: 成功 {stats[model_key]['successful']}, 失败 {stats[model_key]['failed']}")

    # 打印失败列表
    all_failed = []
    for model_key in ["kimi", "qwen", "glm"]:
        all_failed.extend(stats[model_key]["failed_list"])

    if all_failed:
        print("\n失败列表 (前 10 个):")
        for item in all_failed[:10]:
            print(f"  - {item['device_id']}: {item['error']}")
        if len(all_failed) > 10:
            print(f"  ... 还有 {len(all_failed) - 10} 个失败项")


if __name__ == '__main__':
    main()
