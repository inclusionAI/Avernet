#!/usr/bin/env python3
"""下载可用的 bot 信息脚本。

调用 list_connectable_devices_admin 接口获取可连接设备列表，
并保存 bot 信息到 JSON 文件。

使用方法:
    python download_bot_info.py [--output output.json] [--env prod]
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from agentclaw_api_util import fetch_connectable_devices


def build_bot_info_path(env: str) -> str:
    """根据环境构建 bot_info.json 文件路径

    Args:
        env: 环境标识 (pre/prod/gray/dev)

    Returns:
        bot_info.json 文件路径
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, f'bot_info_{env}.json')


def load_device_ids(env: str) -> list[str]:
    """从指定环境的 bot_info.json 文件加载 device_id 列表

    Args:
        env: 环境标识 (pre/prod/gray/dev)

    Returns:
        device_id 列表，失败时返回空列表
    """
    json_file = build_bot_info_path(env)
    device_ids = []
    if not os.path.exists(json_file):
        print(f"[ERROR] JSON 文件不存在：{json_file}")
        return device_ids

    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        items = data.get('items', [])
        for item in items:
            device_id = item.get('device_id')
            if device_id:
                device_ids.append(device_id)
        print(f"共加载 {len(device_ids)} 个 device_id")
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 解析错误：{e}")
    except Exception as e:
        print(f"[ERROR] 读取文件失败：{e}")

    return device_ids


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="下载可用的 bot 信息"
    )
    parser.add_argument(
        "--env", "-e",
        type=str,
        choices=["dev", "pre", "prod", "gray"],
        default='pre',
        help=f"环境标识 (默认：pre)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help=f"输出文件路径 (默认：bot_info_<env>.json)"
    )
    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="页码 (默认：1)"
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="每页数量 (默认：100)"
    )
    parser.add_argument(
        "--with-connection",
        action="store_true",
        help="是否包含连接信息"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="连接端口 (可选)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出详细信息"
    )
    return parser.parse_args()


def extract_bot_info(api_response: dict) -> list[dict]:
    """从 API 响应中提取 bot 信息。

    Args:
        api_response: API 响应数据

    Returns:
        bot 信息列表
    """
    items = api_response.get("data", {}).get("items", [])
    bot_list = []

    for item in items:
        bot_info = {
            "device_id": item.get("device_id"),
            "device_provider": item.get("device_provider"),
            "device_props": item.get("device_props", {}),
            "entity_id": item.get("entity_id"),
            "entity_type": item.get("entity_type"),
            "env": item.get("env"),
            "status": item.get("status"),
            "id": item.get("id"),
            "last_alive_at": item.get("last_alive_at"),
        }

        # 如果有 connection 信息，也一并保存
        if "connection" in item:
            bot_info["connection"] = item.get("connection")

        bot_list.append(bot_info)

    return bot_list


def save_bot_info(bot_list: list[dict], output_file: str, total: int) -> None:
    """保存 bot 信息到 JSON 文件。

    Args:
        bot_list: bot 信息列表
        output_file: 输出文件路径
        total: 总数量
    """
    output_path = Path(output_file)

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        "download_time": datetime.now().isoformat(),
        "total": total,
        "count": len(bot_list),
        "items": bot_list,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    logger.info(f"已保存 {len(bot_list)} 条 bot 信息到：{output_path}")


def main() -> int:
    """主函数"""
    args = parse_args()

    # 根据 env 动态计算默认值
    if not args.output:
        args.output = build_bot_info_path(args.env)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info(f"开始下载 bot 信息")
    logger.info(f"环境：{args.env}")
    logger.info(f"输出文件：{args.output}")

    all_bot_list = []
    total = 0
    page = args.page
    page_size = args.page_size
    consecutive_failures = 0  # 连续失败计数器
    max_failures = 3  # 最大连续失败次数

    # 循环下载所有数据
    while True:
        logger.info(f"正在下载第 {page} 页...")

        # 调用 API 获取数据
        result = fetch_connectable_devices(
            env=args.env,
            page=page,
            page_size=page_size,
            with_connection=args.with_connection,
            port=args.port,
        )

        if result is None:
            consecutive_failures += 1
            logger.warning(f"获取第 {page} 页数据失败 (连续失败 {consecutive_failures}/{max_failures} 次)")
            # 判断是否已获取完所有数据
            if total > 0 and len(all_bot_list) >= total:
                logger.info(f"已获取全部数据，共 {len(all_bot_list)} 条")
                break
            # 如果连续失败次数过多，停止下载
            if consecutive_failures >= max_failures:
                logger.error(f"连续失败 {max_failures} 次，停止下载")
                break
            # 跳过失败页面，继续下一页
            page += 1
            continue
        else:
            consecutive_failures = 0  # 重置失败计数器

        # 提取 bot 信息
        total = result.get("data", {}).get("total", 0)
        bot_list = extract_bot_info(result)
        all_bot_list.extend(bot_list)

        logger.info(f"第 {page} 页获取到 {len(bot_list)} 条 bot 信息（总计：{total}，已获取：{len(all_bot_list)}）")

        # 判断是否还有下一页
        if len(bot_list) < page_size or len(all_bot_list) >= total:
            logger.info(f"下载完成，共 {len(all_bot_list)} 条数据")
            break

        page += 1

    if not all_bot_list:
        logger.warning("未获取到任何 bot 信息")
        return 0

    # 保存到文件
    save_bot_info(all_bot_list, args.output, total)

    logger.info("下载完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
