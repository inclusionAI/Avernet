#!/usr/bin/env python3
"""
G9 Profile Fusion 测试脚本

测试 /api/v1/groups/{group_id}/fuse 接口的 G9 场景，
验证 summary 返回的 markdown 格式是否正确。
"""

import json
import os
import requests
from datetime import datetime


# 配置
BASE_URL = os.environ.get("BCSFUSE_BASE_URL", "http://127.0.0.1:8765")
API_PREFIX = "/api/v1"
AUTH_TOKEN = os.environ.get("BCSFUSE_AUTH_TOKEN", "your-token-here")


def test_g9_fusion():
    """测试 G9 Profile Fusion 场景"""

    # 构建请求
    url = f"{BASE_URL}{API_PREFIX}/groups/grp-test-g9-{datetime.now().strftime('%H%M%S')}/fuse"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}"
    }

    payload = {
        "question": "请分析微服务架构中数据库设计的最佳实践",
        "participants": [
            "expert_risk_analyst",
            "expert_cost_optimizer",
            "expert_quality_master",
            "expert_tech_architect"
        ],
        "fusion_mode": "bot_profile_fuse",
        "options": {
            "timeout_ms": 120000
        }
    }

    print("=" * 80)
    print("G9 Profile Fusion 测试")
    print("=" * 80)
    print(f"URL: {url}")
    print(f"Payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")
    print()

    # 发送请求
    print("发送请求中（超时 600 秒）...")
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=600)
        print(f"响应状态码: {response.status_code}")
    except requests.exceptions.Timeout:
        print("❌ 请求超时")
        return
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return

    # 解析响应
    try:
        result = response.json()
    except json.JSONDecodeError:
        print(f"❌ 响应不是有效的 JSON: {response.text[:500]}")
        return

    # 打印基本信息
    print()
    print("=" * 80)
    print("响应基本信息")
    print("=" * 80)
    print(f"fusion_id: {result.get('fusion_id')}")
    print(f"group_id: {result.get('group_id')}")
    print(f"fusion_mode: {result.get('fusion_mode')}")
    print(f"partial_success: {result.get('partial_success')}")
    print(f"warnings: {result.get('warnings', [])}")
    print(f"errors: {result.get('errors', [])}")

    # 打印 summary
    print()
    print("=" * 80)
    print("Summary 内容（Markdown 渲染后效果）")
    print("=" * 80)

    recommendation = result.get("recommendation")
    if recommendation:
        summary = recommendation.get("summary", "")
        if summary:
            # 直接打印，让终端渲染 markdown
            print(summary)
            print()
            print("-" * 80)
            print(f"Summary 长度: {len(summary)} 字符")
            print(f"换行符数量: {summary.count(chr(10))}")
        else:
            print("❌ summary 为空")
    else:
        print("❌ recommendation 为空")

    # 打印 fused_profile 信息
    print()
    print("=" * 80)
    print("Fused Profile 信息")
    print("=" * 80)
    fused_profile = result.get("fused_profile")
    if fused_profile:
        print(f"name: {fused_profile.get('name')}")
        print(f"description: {fused_profile.get('description', '')[:100]}...")
        print(f"source_participants: {fused_profile.get('source_participants', [])}")
        print(f"skills count: {len(fused_profile.get('skills', []))}")
    else:
        print("fused_profile 为空")

    # 打印 timing
    print()
    print("=" * 80)
    print("Timing 信息")
    print("=" * 80)
    timing = result.get("timing", {})
    print(f"duration_ms: {timing.get('duration_ms')} ms")

    # 保存完整响应到文件
    output_file = "tests/smoke/g9_response.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n完整响应已保存到: {output_file}")


if __name__ == "__main__":
    test_g9_fusion()