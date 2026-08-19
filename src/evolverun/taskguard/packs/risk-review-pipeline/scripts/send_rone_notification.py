#!/usr/bin/env python3
"""
发送Rone卡片通知 - 使用 mcp_adapter（支持多人发送）

与旧skill marketing-flow-init-hy 步骤16对齐：
- 必须传入 activity_id, risk_level, activity_name 参数
- 按风险等级生成差异化通知文案
- 链接使用 roneai-check.antgroup-inc.cn 含 record_id
- 支持多人发送，默认发送给 448158

用法:
    python3 send_rone_notification.py <record_id> --activity-id <id> --risk-level <level> --activity-name <name>
    python3 send_rone_notification.py 800037 --activity-id 300005 --risk-level 低风险 --activity-name 红包码核心权益区支付券包
    python3 send_rone_notification.py 800037 --activity-id 300005 --risk-level 高风险 --activity-name xxx --staff-ids 461514 WB01771939

输出:
    {"success": true, "message": "Rone卡片发送成功", "record_id": "800037", "sent_count": 3, "total_count": 3, "results": [...]}
"""

import json
import os
import sys
import time

from mcp_adapter import send_rone_card

# 默认通知目标用户列表
DEFAULT_STAFF_IDS = ['448158']


def build_notification_text(activity_id: str, risk_level: str, activity_name: str) -> str:
    """根据风险等级生成差异化通知文案（对齐旧skill步骤16）"""
    if risk_level == '高风险':
        return (
            f'[活动{activity_id}] 智能评审小助手提醒您，'
            f'营销活动"{activity_name}"智能评审判定活动配置风险较高，请人工介入评审。'
        )
    else:
        return (
            f'[活动{activity_id}] 智能评审小助手提醒您，'
            f'营销活动"{activity_name}"已完成智能评审，活动配置风险较低，已智能评审通过。'
        )


def send_rone_card_single(record_id: str, activity_id: str, risk_level: str, activity_name: str, staff_id: str) -> dict:
    """
    向单个用户发送Rone卡片通知

    Args:
        record_id: Rone记录ID
        activity_id: 活动ID（避免多条通知被合并）
        risk_level: 风险等级（"低风险" 或 "高风险"）
        activity_name: 活动名称
        staff_id: 目标用户工号

    Returns:
        dict: {"success": bool, "staffId": str, "error": str (optional)}
    """
    text = build_notification_text(activity_id, risk_level, activity_name)
    title_url_key = f'Rone评审链接{record_id}'
    title_url_value = f'https://roneai-check.antgroup-inc.cn/activity?id={record_id}'

    from mcp_adapter import to_compact_json, MCP_SERVER, MCP_TOOL, _get_mcporter_cwd

    content_dict = {
        'staffId': staff_id,
        'text': text,
        'titleUrlMap': {
            title_url_key: title_url_value,
        },
    }
    content_str = json.dumps(content_dict, ensure_ascii=False, separators=(',', ':'))

    args_dict = {
        'evalMaterial/evalType': 'CONTACT',
        'evalMaterial/evaSubType': 'roneCard',
        'evalMaterial/evalContent': content_str,
    }
    args_json = json.dumps(args_dict, ensure_ascii=False)
    cmd_list = ['mcporter', 'call', '--server', MCP_SERVER, MCP_TOOL, '--args', args_json]

    try:
        from mcp_adapter import call_mcp
        result = call_mcp(cmd_list, warmup=True)
        if not result['success']:
            return {'success': False, 'staffId': staff_id, 'error': result['error']}

        inner_data = result['data']
        if isinstance(inner_data, dict) and inner_data.get('success'):
            return {'success': True, 'staffId': staff_id}
        error_msg = (inner_data.get('message') or inner_data.get('error') or '发送失败') if isinstance(inner_data, dict) else '发送失败'
        return {'success': False, 'staffId': staff_id, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'staffId': staff_id, 'error': f'发送失败: {str(e)}'}


def send_rone_card_with_info(record_id: str, activity_id: str, risk_level: str, activity_name: str, staff_ids: list = None) -> dict:
    """
    向多个用户发送Rone卡片通知

    Args:
        record_id: Rone记录ID
        activity_id: 活动ID（避免多条通知被合并）
        risk_level: 风险等级（"低风险" 或 "高风险"）
        activity_name: 活动名称
        staff_ids: 目标用户工号列表，默认为 DEFAULT_STAFF_IDS

    Returns:
        dict: {"success": bool, "message": str, "record_id": str, "sent_count": int, "total_count": int, "results": list}
    """
    if staff_ids is None:
        staff_ids = DEFAULT_STAFF_IDS

    results = []
    for staff_id in staff_ids:
        result = send_rone_card_single(record_id, activity_id, risk_level, activity_name, staff_id)
        results.append(result)
        # 多人发送间隔，避免速率限制
        if staff_id != staff_ids[-1]:
            time.sleep(0.5)

    success_count = sum(1 for r in results if r['success'])
    all_success = success_count == len(staff_ids)

    if all_success:
        message = f'Rone卡片发送成功，{success_count}/{len(staff_ids)}人已通知'
    else:
        failed_ids = [r['staffId'] for r in results if not r['success']]
        message = f'部分发送失败({success_count}/{len(staff_ids)})，失败: {",".join(failed_ids)}'

    return {
        'success': all_success,
        'message': message,
        'record_id': record_id,
        'sent_count': success_count,
        'total_count': len(staff_ids),
        'results': results,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description='发送Rone卡片通知')
    parser.add_argument('--record-id', '-d', type=str,
                        help='Rone记录ID（也可作为位置参数传入）')
    parser.add_argument('--activity-id', '-a', type=str, required=True,
                        help='活动ID（避免多条通知被合并）')
    parser.add_argument('--risk-level', '-r', type=str, required=True,
                        choices=['低风险', '高风险'],
                        help='风险等级（低风险 或 高风险）')
    parser.add_argument('--activity-name', '-n', type=str, required=True,
                        help='活动名称')
    parser.add_argument('--staff-ids', '-s', type=str, nargs='+',
                        default=DEFAULT_STAFF_IDS,
                        help=f'通知目标用户工号列表，默认: {" ".join(DEFAULT_STAFF_IDS)}')
    # Support both positional and --record-id; also accept leftover positional arg
    known, remaining = parser.parse_known_args()
    if known.record_id is None and remaining:
        known.record_id = remaining[0]
    if not known.record_id:
        parser.error('record_id is required (use --record-id or pass as positional argument)')
    args = known

    result = send_rone_card_with_info(
        args.record_id, args.activity_id, args.risk_level, args.activity_name,
        staff_ids=args.staff_ids
    )

    print(json.dumps(result, ensure_ascii=False))

    if not result['success']:
        sys.exit(1)


if __name__ == '__main__':
    main()