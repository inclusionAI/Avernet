# -*- coding: utf-8 -*-
"""
营销智评数据预处理模块
输入：CSV文件（含 user_id 和 event_property 两列）
输出：preprocessed_data.json

JSON清洗逻辑基于 input.sql 的 REGEXP_REPLACE 系列
"""

import json
import re
import os
import sys
import argparse
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

# 添加父目录到路径以导入 prize_value_recognition
# 沙箱环境可能部署在 skills-local/ 或 skills/ 下，需同时搜索两个目录
_this_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_this_dir)
_SANDBOX_WORKSPACE = "/home/admin/.openclaw/workspace"
_parent_dirs = [_parent_dir]
_basename = os.path.basename(_parent_dir.rstrip('/'))
if _basename == 'skills-local':
    _sibling = os.path.join(os.path.dirname(_parent_dir), 'skills')
elif _basename == 'skills':
    _sibling = os.path.join(os.path.dirname(_parent_dir), 'skills-local')
else:
    _sibling = None
if _sibling and os.path.isdir(_sibling) and _sibling not in _parent_dirs:
    _parent_dirs.append(_sibling)
for _std in [os.path.join(_SANDBOX_WORKSPACE, 'skills-local'),
             os.path.join(_SANDBOX_WORKSPACE, 'skills')]:
    if os.path.isdir(_std) and _std not in _parent_dirs:
        _parent_dirs.append(_std)
for _d in _parent_dirs:
    if _d not in sys.path:
        sys.path.insert(0, _d)
from prize_value_recognition.processor import classify_prize_benefit, get_prize_true_value, determine_prize_value_level

# ============================================================================
# 配置
# ============================================================================

# 四同维度映射
DIMENSION_MAP = {
    "ID_CARD_NO": "同证件号",
    "USER_ID": "同支付宝账户",
    "PHONE_NO": "同手机号",
    "TERMINAL_ID": "同手机设备"
}

# 频次控制类型映射
FREQUENCY_TYPE_MAP = {
    "D": "每天",
    "W": "每周",
    "M": "每月",
    "Y": "每年"
}

# 人群限制类型映射
CROWD_LIMIT_MAP = {
    "CROWDRULEID": "按人群规则限制",
    "notLimit": "无限制",
    "COMMONCONFIG": "限制用户实名"
}

# 触发类型映射
TRIGGER_TYPE_MAP = {
    "RECOMMEND_RECEIVE_TRIGGER": "推荐领奖活动(推荐领奖)",
    "USER_TRIGGER": "用户触发",
    "SYSTEM_TRIGGER": "系统触发"
}

# ============================================================================
# 工具函数
# ============================================================================

def _safe_get_exam(data: Dict) -> Dict:
    """安全获取examinationBasicInfo，处理值为字符串的情况"""
    exam = data.get('examinationBasicInfo', {})
    if isinstance(exam, str):
        exam = safe_json_loads(exam) or {}
    return exam if isinstance(exam, dict) else {}


def _safe_get_cbin(exam: Dict) -> Dict:
    """安全获取campBasicInfo，兼容 campBasicInfoNew 和 campBasicInfo_new 两种key格式"""
    cbin = exam.get('campBasicInfoNew') or exam.get('campBasicInfo_new') or {}
    if isinstance(cbin, str):
        cbin = safe_json_loads(cbin) or {}
    return cbin if isinstance(cbin, dict) else {}


def _safe_get_dc(exam: Dict) -> Dict:
    """安全获取detailContent（从campBasicInfo中提取并解析）"""
    cbin = _safe_get_cbin(exam)
    dc = cbin.get('detailContent', {})
    if isinstance(dc, str):
        dc = safe_json_loads(dc) or {}
    return dc if isinstance(dc, dict) else {}


def _safe_get_prize_basic_all(data: Dict) -> List:
    """安全获取prizeBasicInfoAll，处理嵌套字符串"""
    exam = _safe_get_exam(data)
    pba = exam.get('prizeBasicInfoAll', [])
    if isinstance(pba, str):
        pba = safe_json_loads(pba) or []
    return pba if isinstance(pba, list) else []

def clean_json_string(json_str: str) -> str:
    r"""
    清洗JSON字符串

    基于 input.sql 的 REGEXP_REPLACE 逻辑：
    REGEXP_REPLACE(
        REGEXP_REPLACE(
            REGEXP_REPLACE(REPLACE(REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(
                REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(
                    event_property, '\\\\"', '"'), '\\\\n', '\n'), '\\\\t', '\t'),
                    '\\\\\\\\', '\\'), '\\\\', ''), '"\{', '\{'), '\}"', '\}'),
                    '"[', '['), ']"', ']'), '\\s{2,}', ' ')
            ,'\\n', '')
        ,'<span[^>]*>([\\s\\S]*?)</span>', '', 0)
    """
    if not json_str:
        return json_str

    text = str(json_str)

    # 步骤0: 移除原始控制字符（ord < 32，但保留 \n \r 用于后续处理）
    # 注意：JSON字符串值中不能包含原始的控制字符（如tab, ord=9），必须转义
    text = ''.join(c for c in text if ord(c) >= 32 or c in '\n\r')

    # 步骤1: 处理转义序列（按 SQL 顺序）
    text = text.replace('\\"', '"')      # \\" → "
    text = text.replace('\\n', '\n')     # \\n → 换行符
    text = text.replace('\\t', '\t')     # \\t → 制表符
    text = text.replace('\\\\', '\\')    # \\\\ → \
    text = text.replace('\\', '')        # \ → 空

    # 步骤2: 处理 JSON 边界的引号
    text = text.replace('"{', '{')
    text = text.replace('}"', '}')
    text = text.replace('"[', '[')
    text = text.replace(']"', ']')

    # 步骤3: 去除多余空格
    text = re.sub(r'\s{2,}', ' ', text)

    # 步骤4: 去除换行符
    text = text.replace('\n', '')

    # 步骤5: 去除 HTML <span> 标签
    text = re.sub(r'<span[^>]*>([\s\S]*?)</span>', '', text)


    return text


def escape_unescaped_quotes(text: str) -> str:
    """
    转义JSON字符串值内部的未转义引号

    场景：JSON字符串值内部有未转义的引号，如：
    "planBackground":"开展"首登有礼"活动" → "planBackground":"开展\"首登有礼\"活动"

    策略：扫描 :" 后的字符串值，转义其中未转义的引号
    """
    result = []
    i = 0
    while i < len(text):
        # 查找 :" 模式（key结束，value开始）
        if i > 0 and text[i-1] == ':' and text[i] == '"':
            result.append(text[i])  # 添加开始的引号
            i += 1
            # 扫描字符串值
            while i < len(text):
                if text[i] == '\\' and i + 1 < len(text) and text[i+1] in '"\\':
                    # 已转义的字符，保留
                    result.append(text[i:i+2])
                    i += 2
                elif text[i] == '"':
                    # 检查后面是否跟着 , } ] : 或空白（字符串结束标记）
                    if i + 1 >= len(text) or text[i+1] in ',}]' or text[i+1].isspace():
                        # 这是字符串结束引号
                        break
                    else:
                        # 这是内容中的引号，转义它
                        result.append('\\"')
                        i += 1
                else:
                    result.append(text[i])
                    i += 1

            if i < len(text):
                result.append(text[i])  # 添加结束引号
                i += 1
        else:
            result.append(text[i])
            i += 1

    return ''.join(result)


def safe_json_loads(json_str: str, max_retries: int = 3) -> Any:
    """
    安全解析JSON字符串，带重试和清洗机制
    """
    if not json_str:
        return None

    # 第一次尝试：直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 第二次尝试：使用清洗逻辑解析
    try:
        cleaned = clean_json_string(json_str)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第三次尝试：清洗后移除控制字符
    try:
        cleaned = clean_json_string(json_str)
        cleaned = ''.join(c for c in cleaned if ord(c) >= 32 or c in '\n\r')
        cleaned = cleaned.replace('\n', '').replace('\r', '')
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第四次尝试：修复JSON字符串值中的引号混用问题
    # 场景：JSON字符串值内部的中文引号或未转义ASCII引号
    # 策略：先将中文引号替换为ASCII引号，再转义字符串值内的未转义引号
    try:
        cleaned = clean_json_string(json_str)
        cleaned = ''.join(c for c in cleaned if ord(c) >= 32 or c in '\n\r')
        cleaned = cleaned.replace('\n', '').replace('\r', '')
        # 将中文引号替换为ASCII引号
        cleaned = cleaned.replace(chr(8220), '"').replace(chr(8221), '"')
        # 转义JSON字符串值内的未转义引号
        cleaned = escape_unescaped_quotes(cleaned)
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"JSON解析最终失败: {e}")
        return None


# ============================================================================
# 活动总抽奖期望计算
# ============================================================================

def calculate_prize_expected_value(prize_list: List[Dict], prize_values: Dict[str, float]) -> float:
    """
    计算每次抽奖的期望价值

    公式: Σ(奖品价值 × 奖品权重) / Σ(奖品权重)

    Args:
        prize_list: 奖品列表，每个奖品包含 prizeId, weight 等
        prize_values: 奖品价值字典，{prizeId: value}

    Returns:
        每次抽奖的期望价值
    """
    if not prize_list:
        return 0.0

    total_weight = 0.0
    total_value = 0.0

    for prize in prize_list:
        prize_id = prize.get('prizeId', '')
        weight_str = prize.get('weight', '1')

        # 权重转换：如果为空或0，默认为1
        try:
            weight = float(weight_str) if weight_str and float(weight_str) > 0 else 1.0
        except (ValueError, TypeError):
            weight = 1.0

        # 获取奖品价值
        value = prize_values.get(prize_id, 0.0)

        total_weight += weight
        total_value += value * weight

    return total_value / total_weight if total_weight > 0 else 0.0


def get_activity_limit_count(data: Dict) -> Dict[str, Any]:
    """
    获取活动粒度的限制次数

    Returns:
        {
            'has_limit': bool,  # 是否有任何活动粒度限制
            'participate_count': int or None,  # 参与次数限制（终身）
            'participate_frequency': int or None,  # 参与频次限制
            'prize_count': int or None,  # 中奖次数限制（终身）
            'prize_frequency': int or None,  # 中奖频次限制
        }
    """
    try:
        dc = _safe_get_dc(_safe_get_exam(data))
    except (KeyError, TypeError):
        return {'has_limit': False, 'participate_count': None, 'participate_frequency': None,
                'prize_count': None, 'prize_frequency': None}

    result = {
        'has_limit': False,
        'participate_count': None,
        'participate_frequency': None,
        'prize_count': None,
        'prize_frequency': None,
    }

    # 参与次数限制
    count_limit_model = dc.get('countLimitModel', {})
    if count_limit_model.get('limitControl') == 'limit':
        result['participate_count'] = count_limit_model.get('limitCount', 0)
        result['has_limit'] = True

    # 参与频次限制
    count_freq_model = dc.get('countFrequencyModel', {})
    if count_freq_model.get('userFrequencyLimitControl') == 'limit':
        result['participate_frequency'] = count_freq_model.get('userFrequencyLimit', 0)
        result['has_limit'] = True

    # 中奖次数限制
    prize_limit_model = dc.get('prizeCountLimitModel', {})
    if prize_limit_model.get('limitControl') == 'limit':
        result['prize_count'] = prize_limit_model.get('limitCount', 0)
        result['has_limit'] = True

    # 中奖频次限制
    prize_freq_model = dc.get('prizeCountFrequencyModel', {})
    if prize_freq_model.get('userFrequencyLimitControl') == 'limit':
        result['prize_frequency'] = prize_freq_model.get('userFrequencyLimit', 0)
        result['has_limit'] = True

    return result


def calculate_total_draw_count(limit_info: Dict[str, Any]) -> int or None:
    """
    计算可抽奖总次数（活动粒度）

    规则：
    - 有次数限制（参与次数/中奖次数）→ min(参与次数, 中奖次数)
    - 无次数限制但有频率限制 → max(参与频率, 中奖频率)
    - 无任何活动粒度限制 → 返回 None

    Args:
        limit_info: get_activity_limit_count() 的返回值

    Returns:
        可抽奖总次数，若无限制返回 None
    """
    if not limit_info.get('has_limit'):
        return None

    # 次数限制（参与次数、中奖次数）
    count_limits = []
    if limit_info.get('participate_count') is not None:
        count_limits.append(limit_info['participate_count'])
    if limit_info.get('prize_count') is not None:
        count_limits.append(limit_info['prize_count'])

    # 频率限制（参与频率、中奖频率）
    freq_limits = []
    if limit_info.get('participate_frequency') is not None:
        freq_limits.append(limit_info['participate_frequency'])
    if limit_info.get('prize_frequency') is not None:
        freq_limits.append(limit_info['prize_frequency'])

    if count_limits:
        # 有次数限制 → min(参与次数, 中奖次数)
        return min(count_limits)
    elif freq_limits:
        # 无次数限制，有频率限制 → max(参与频率, 中奖频率)
        return max(freq_limits)

    return None


def get_prize_level_limits(data: Dict) -> Dict[str, int]:
    """
    获取奖品粒度的终身次数限制

    奖品粒度限制字段说明（countControlConfigDTOs）：
    - lifeCount: 终身次数限制（终身可中奖次数）
    - yearCount: 年频次限制（每年可中奖次数，0=无限制）
    - monthCount: 月频次限制（每月可中奖次数，0=无限制）
    - weekCount: 周频次限制（每周可中奖次数，0=无限制）
    - dayCount: 日频次限制（每天可中奖次数，0=无限制）

    Returns:
        {prizeId: lifeCount, ...}  只返回终身次数限制 > 0 的奖品
    """
    result = {}

    try:
        prize_basic_all = _safe_get_prize_basic_all(data)
    except (KeyError, TypeError):
        return result

    for prize_info in prize_basic_all:
        try:
            prize_id = prize_info['prizeBaseInfoDTO']['prizeId']
            count_controls = prize_info.get('countControlConfigDTOs', [])

            if count_controls:
                # 取第一个配置的终身限制（次数限制）
                life_count = count_controls[0].get('lifeCount', 0)
                if life_count > 0:
                    result[prize_id] = life_count
        except (KeyError, TypeError, IndexError):
            continue

    return result


def get_prize_level_frequency_limits(data: Dict) -> Dict[str, Dict[str, int]]:
    """
    获取奖品粒度的频次限制

    Returns:
        {
            prizeId: {
                'day': int,      # 日频次限制，0=无限制
                'week': int,     # 周频次限制，0=无限制
                'month': int,    # 月频次限制，0=无限制
                'year': int,     # 年频次限制，0=无限制
            },
            ...
        }
    """
    result = {}

    try:
        prize_basic_all = _safe_get_prize_basic_all(data)
    except (KeyError, TypeError):
        return result

    for prize_info in prize_basic_all:
        try:
            prize_id = prize_info['prizeBaseInfoDTO']['prizeId']
            count_controls = prize_info.get('countControlConfigDTOs', [])

            if count_controls:
                ctrl = count_controls[0]
                day_count = ctrl.get('dayCount', 0)
                week_count = ctrl.get('weekCount', 0)
                month_count = ctrl.get('monthCount', 0)
                year_count = ctrl.get('yearCount', 0)

                # 只返回有频次限制的奖品
                if day_count > 0 or week_count > 0 or month_count > 0 or year_count > 0:
                    result[prize_id] = {
                        'day': day_count,
                        'week': week_count,
                        'month': month_count,
                        'year': year_count,
                    }
        except (KeyError, TypeError, IndexError):
            continue

    return result


def calculate_expected_value_without_limit(prize_list: List[Dict],
                                            prize_values: Dict[str, float],
                                            prize_limits: Dict[str, int]) -> float:
    """
    计算无活动粒度限制时的活动总抽奖期望

    公式: Σ(奖品价值 × 奖品粒度终身次数限制)

    Args:
        prize_list: 奖品列表
        prize_values: 奖品价值字典 {prizeId: value}
        prize_limits: 奖品粒度限制字典 {prizeId: lifeCount}

    Returns:
        活动总抽奖期望
    """
    total = 0.0

    for prize in prize_list:
        prize_id = prize.get('prizeId', '')
        value = prize_values.get(prize_id, 0.0)
        limit = prize_limits.get(prize_id, 0)

        total += value * limit

    return total


def calculate_activity_expected_value(data: Dict, prize_values: Dict[str, float]) -> Dict[str, Any]:
    """
    计算活动总抽奖期望（主函数）

    Args:
        data: 解析后的活动数据（包含 examinationBasicInfo）
        prize_values: 奖品价值字典 {prizeId: value}

    Returns:
        {
            'has_activity_limit': bool,  # 是否有活动粒度限制
            'prize_expected_value': float,  # 每次抽奖期望
            'total_draw_count': int or None,  # 可抽奖总次数（None表示无限制）
            'activity_expected_value': float,  # 活动总抽奖期望
            'calculation_method': str,  # 计算方法描述
        }
    """
    try:
        dc = _safe_get_dc(_safe_get_exam(data))
        prize_config = dc.get('prizeConfigModel', {})
        prize_list = prize_config.get('prizeList', [])
    except (KeyError, TypeError):
        return {
            'has_activity_limit': False,
            'prize_expected_value': 0.0,
            'total_draw_count': None,
            'activity_expected_value': 0.0,
            'calculation_method': '数据缺失',
        }

    # 1. 计算每次抽奖期望
    prize_expected_value = calculate_prize_expected_value(prize_list, prize_values)

    # 2. 获取活动粒度限制
    limit_info = get_activity_limit_count(data)
    has_activity_limit = limit_info.get('has_limit', False)

    # 3. 计算结果
    if has_activity_limit:
        # 有活动粒度限制
        total_draw_count = calculate_total_draw_count(limit_info)
        if total_draw_count is not None:
            activity_expected_value = prize_expected_value * total_draw_count
            calculation_method = f"每次抽奖期望({prize_expected_value:.4f}) × 可抽奖次数({total_draw_count})"
        else:
            activity_expected_value = prize_expected_value
            total_draw_count = 1
            calculation_method = "活动粒度限制为0，取每次抽奖期望"
    else:
        # 无活动粒度限制，使用奖品粒度终身限制
        prize_limits = get_prize_level_limits(data)
        activity_expected_value = calculate_expected_value_without_limit(prize_list, prize_values, prize_limits)
        total_draw_count = None
        calculation_method = "无活动粒度限制，Σ(奖品价值 × 奖品粒度终身限制)"

    return {
        'has_activity_limit': has_activity_limit,
        'prize_expected_value': round(prize_expected_value, 4),
        'total_draw_count': total_draw_count,
        'activity_expected_value': round(activity_expected_value, 4),
        'calculation_method': calculation_method,
        'limit_info': limit_info,
    }


def format_limit_info(limit_info: Dict[str, Any]) -> str:
    """
    格式化限制信息为人类可读字符串

    Args:
        limit_info: get_activity_limit_count() 的返回值

    Returns:
        格式化后的字符串，如 "同支付宝账户，终身2次"
    """
    if not limit_info.get('has_limit'):
        return "无活动粒度限制"

    parts = []

    if limit_info.get('participate_count') is not None:
        parts.append(f"参与次数限制: {limit_info['participate_count']}次")

    if limit_info.get('participate_frequency') is not None:
        parts.append(f"参与频次限制: {limit_info['participate_frequency']}次")

    if limit_info.get('prize_count') is not None:
        parts.append(f"中奖次数限制: {limit_info['prize_count']}次")

    if limit_info.get('prize_frequency') is not None:
        parts.append(f"中奖频次限制: {limit_info['prize_frequency']}次")

    return "; ".join(parts) if parts else "无活动粒度限制"


# ============================================================================
# 奖品价值提取（调用 prize_value_recognition 模块）
# ============================================================================

def _extract_prize_values_from_prize_list(data, scenarios,
                                          camp_name: str, plan_name: str) -> Dict[str, Dict[str, Any]]:
    """
    兜底：当 prizeBasicInfoAll 为空时，从 detailContent.prizeConfigModel.prizeList 提取奖品基本信息。
    prizeList 只有 prizeId/prizeName/prizeType/weight/worth，没有 priceStrategyDTO 等详细字段。
    """
    result = {}
    try:
        dc = _safe_get_dc(_safe_get_exam(data))
        pcm = dc.get('prizeConfigModel', {})
        if isinstance(pcm, str):
            pcm = safe_json_loads(pcm) or {}
        prize_list = pcm.get('prizeList', [])
        if isinstance(prize_list, str):
            prize_list = safe_json_loads(prize_list) or []
    except (KeyError, TypeError):
        return result

    for prize in prize_list:
        try:
            prize_id = prize.get('prizeId', '')
            if not prize_id:
                continue
            prize_name = prize.get('prizeName', '')
            prize_type = prize.get('prizeType', '')

            benefit_type = classify_prize_benefit(
                scenarios=scenarios,
                prize_type=prize_type,
                prize_sub_type='',
                voucher_product_code='',
                prize_name=prize_name,
                camp_name=camp_name,
                plan_name=plan_name,
                voucher_template_name=''
            )

            # prizeList 没有 maxPrice，无法精确计算价值，用0
            result[prize_id] = {
                'prize_name': prize_name,
                'benefit_type': benefit_type,
                'true_value': 0,
                'value_level': '低',
                'value_desc': '兜底(prizeList无价格信息)',
                'max_price_raw': 0,
                'min_price_raw': 0,
            }
        except Exception:
            continue

    return result


def extract_prize_values_from_data(data: Dict, scenarios: List[str] = None, camp_name: str = "", plan_name: str = "") -> Dict[str, Dict[str, Any]]:
    """
    从 JSON 数据中提取奖品信息并计算价值

    Args:
        data: 解析后的活动数据（包含 examinationBasicInfo）
        scenarios: 业务场景列表（如 ['财富'], ['消金', '财富']），用于权益类型分类

    Returns:
        {
            prizeId: {
                'prize_name': str,           # 奖品名称
                'benefit_type': str,         # 权益类型
                'true_value': float,         # 真实价值（元）
                'value_level': str,          # 价值分档：高/中/低
                'value_desc': str,           # 价值来源说明
                'max_price_raw': float,      # 原始 maxPrice
                'min_price_raw': float,      # 原始 minPrice
            },
            ...
        }
    """
    if scenarios is None:
        scenarios = []

    result = {}

    try:
        prize_basic_all = _safe_get_prize_basic_all(data)
    except (KeyError, TypeError):
        return result

    if not prize_basic_all:
        # 兜底：从 prizeConfigModel.prizeList 提取基本信息
        return _extract_prize_values_from_prize_list(data, scenarios, camp_name, plan_name)

    # 获取券模板信息（用于 voucherProductCode）
    voucher_info_map = {}
    try:
        voucher_basic_info = _safe_get_exam(data).get('prizeVoucherBasicInfo', [])
        for v in voucher_basic_info:
            template_id = v.get('templateId')
            if template_id:
                voucher_info_map[template_id] = v
    except (KeyError, TypeError):
        pass

    for prize_info in prize_basic_all:
        try:
            base_info = prize_info.get('prizeBaseInfoDTO', {})
            prize_id = base_info.get('prizeId', '')
            if not prize_id:
                continue

            prize_name = base_info.get('prizeName', '')
            prize_type = base_info.get('prizeType', '')
            prize_sub_type = base_info.get('prizeSubType', '')

            # 获取价格信息
            price_strategy = prize_info.get('priceStrategyDTO', {})
            max_price_raw = float(price_strategy.get('maxPrice', 0) or 0)
            min_price_raw = float(price_strategy.get('minPrice', 0) or 0)

            # 获取券模板信息
            ext_props = prize_info.get('extProperties', {})
            voucher_template_id = ext_props.get('VOUCHER_TEMPLATE_ID', '')

            voucher_product_code = ''
            voucher_template_name = ''
            if voucher_template_id and voucher_template_id in voucher_info_map:
                voucher_info = voucher_info_map[voucher_template_id]
                voucher_product_code = voucher_info.get('productCode', '')
                voucher_template_name = voucher_info.get('templateName', '') or voucher_info.get('voucherName', '')

            # 分类权益类型
            benefit_type = classify_prize_benefit(
                scenarios=scenarios,
                prize_type=prize_type,
                prize_sub_type=prize_sub_type,
                voucher_product_code=voucher_product_code,
                prize_name=prize_name,
                camp_name=camp_name,
                plan_name=plan_name,
                voucher_template_name=voucher_template_name
            )

            # 计算真实价值
            true_value, value_desc = get_prize_true_value(
                prize_name=prize_name,
                max_price_raw=max_price_raw,
                benefit_type=benefit_type
            )

            # 计算价值分档
            value_level = determine_prize_value_level(true_value)

            result[prize_id] = {
                'prize_name': prize_name,
                'benefit_type': benefit_type,
                'true_value': true_value,
                'value_level': value_level,  # 价值分档：高/中/低
                'value_desc': value_desc,
                'max_price_raw': max_price_raw,
                'min_price_raw': min_price_raw,
            }

        except Exception as e:
            # 记录错误但继续处理其他奖品
            print(f"提取奖品价值失败: {e}")
            continue

    return result


def calculate_activity_expected_value_full(data: Dict, scenarios: List[str] = None) -> Dict[str, Any]:
    """
    计算活动总抽奖期望（完整版，自动提取奖品价值）

    这个函数整合了奖品价值提取和活动总抽奖期望计算。

    Args:
        data: 解析后的活动数据（包含 examinationBasicInfo）
        scenarios: 业务场景列表，用于权益类型分类

    Returns:
        {
            'prize_values': Dict,        # 奖品价值字典 {prizeId: {prize_name, true_value, ...}}
            'has_activity_limit': bool,
            'prize_expected_value': float,
            'total_draw_count': int or None,
            'activity_expected_value': float,
            'calculation_method': str,
            'limit_info': Dict,
        }
    """
    # 1. 提取奖品价值
    prize_values_detail = extract_prize_values_from_data(data, scenarios)

    # 2. 构建简化字典 {prizeId: true_value}
    prize_values = {prize_id: info['true_value'] for prize_id, info in prize_values_detail.items()}

    # 3. 计算活动总抽奖期望
    expected_result = calculate_activity_expected_value(data, prize_values)

    # 4. 整合结果
    expected_result['prize_values'] = prize_values_detail

    return expected_result


# ============================================================================
# Pipeline 标准化输出
# ============================================================================

# 四个维度常量
_ALL_DIMS = {'USER_ID', 'PHONE_NO', 'ID_CARD_NO', 'TERMINAL_ID'}


def _parse_activity_controls_raw(dc: Dict) -> List[Dict]:
    """从 detailContent 解析活动粒度4个频次控制范围"""
    controls = []

    # 1. 参与次数 countLimitModel
    m = dc.get('countLimitModel', {})
    if m.get('limitControl') == 'limit':
        dims = m.get('limitDimension', [])
        cnt = m.get('limitCount', 0)
        if isinstance(dims, list) and cnt and cnt > 0:
            controls.append({'name': '参与次数', 'dim_count': len(set(dims) & _ALL_DIMS), 'count': cnt, 'dimensions': dims})

    # 2. 参与频次 countFrequencyModel
    m = dc.get('countFrequencyModel', {})
    if m.get('userFrequencyLimitControl', 'notLimit') != 'notLimit':
        dims = m.get('countFrequencyDimension', [])
        cnt = m.get('userFrequencyLimit', 0)
        if isinstance(dims, list) and cnt and cnt > 0:
            controls.append({'name': '参与频次', 'dim_count': len(set(dims) & _ALL_DIMS), 'count': cnt, 'dimensions': dims})

    # 3. 中奖次数 prizeCountLimitModel
    m = dc.get('prizeCountLimitModel', {})
    if m.get('limitControl') == 'limit':
        dims = m.get('limitDimension', [])
        cnt = m.get('limitCount', 0)
        if isinstance(dims, list) and cnt and cnt > 0:
            controls.append({'name': '中奖次数', 'dim_count': len(set(dims) & _ALL_DIMS), 'count': cnt, 'dimensions': dims})

    # 4. 中奖频次 prizeCountFrequencyModel
    m = dc.get('prizeCountFrequencyModel', {})
    if m.get('userFrequencyLimitControl', 'notLimit') != 'notLimit':
        dims = m.get('countFrequencyDimension', [])
        cnt = m.get('userFrequencyLimit', 0)
        if isinstance(dims, list) and cnt and cnt > 0:
            controls.append({'name': '中奖频次', 'dim_count': len(set(dims) & _ALL_DIMS), 'count': cnt, 'dimensions': dims})

    return controls


def _parse_prize_controls_raw(prize_info: Dict) -> Dict:
    """解析单个奖品的频次控制"""
    ccs = prize_info.get('countControlConfigDTOs', [])
    if not ccs:
        return {'dim_count': 0, 'count': 0}

    life_dims = set()
    freq_dims = set()
    life_counts = []
    freq_counts = []

    for cc in ccs:
        dim = cc.get('dimension', '')
        if dim not in _ALL_DIMS:
            continue
        life = cc.get('lifeCount', 0) or 0
        day = cc.get('dayCount', 0) or 0
        week = cc.get('weekCount', 0) or 0
        month = cc.get('monthCount', 0) or 0
        year = cc.get('yearCount', 0) or 0

        if life > 0:
            life_dims.add(dim)
            life_counts.append(life)
        if day > 0 or week > 0 or month > 0 or year > 0:
            freq_dims.add(dim)
            freq_counts.append(max(day, week, month, year))

    if len(life_dims) >= len(freq_dims):
        dim_count = len(life_dims)
        count = max(life_counts) if life_counts else 0
    else:
        dim_count = len(freq_dims)
        count = max(freq_counts) if freq_counts else 0

    if len(life_dims) == len(freq_dims) and life_counts:
        count = max(life_counts)

    return {'dim_count': dim_count, 'count': count}


def preprocess_row(ep_str: str) -> Dict:
    """
    Pipeline 第1步：将原始 event_property JSON 解析为标准化字典。

    所有下游模块只读这个字典，不再碰原始JSON。

    Returns:
        {
            # 基本信息
            'activity_id': str,
            'camp_name': str,
            'bu_name': str,
            'plan_name': str,
            'environment': str,       # PRODUCTION_ENV / PRE_ENV

            # 频次控制 - 活动粒度
            'activity_controls': [{name, dim_count, count, dimensions}],

            # 奖品列表（含频次控制）
            'prizes': [{
                'prize_id', 'prize_name', 'prize_type', 'prize_sub_type',
                'max_price_raw', 'min_price_raw', 'weight',
                'controls': {dim_count, count},
            }],
            'prize_count': int,

            # 风控字段
            'ctu_validate_level': str,   # REAL_TIME/OFFLINE/NONE/NOT_FOUND
            'skip_ctu_validate': str,    # true/false/missing

            # 实名/人群
            'realname_auth': int,        # 0 or 1
            'crowd_limit_type': str,     # notLimit / CROWDRULEID / ...

            # PL/CP 时间
            'pl_gmt_end': int or None,
            'cp_gmt_end': int or None,

            # 活动总抽奖期望
            'activity_expected_value': float,

            # 原始解析数据（供复杂处理用）
            '_raw_data': dict,
            '_raw_ep_str': str,
        }
    """
    data = safe_json_loads(ep_str)
    if not data:
        return {'_parse_failed': True, '_raw_ep_str': ep_str}

    exam = _safe_get_exam(data)
    cbin = _safe_get_cbin(exam)
    dc = _safe_get_dc(exam)

    # 基本信息
    activity_id = exam.get('activityId', '') or ''

    # campName 三层 fallback 逻辑（与SQL一致）
    # 优先级1: campBasicInfo.campBaseInfoDTO.campName
    # 优先级2: campBasicInfoNew.campName
    # 优先级3: 顶层 campName
    camp_name = ''
    try:
        cbi = exam.get('campBasicInfo', {})
        if isinstance(cbi, str):
            cbi = safe_json_loads(cbi) or {}
        cbd = cbi.get('campBaseInfoDTO', {})
        if isinstance(cbd, str):
            cbd = safe_json_loads(cbd) or {}
        camp_name = cbd.get('campName', '') or ''
    except (KeyError, TypeError):
        pass
    if not camp_name:
        try:
            camp_name = cbin.get('campName', '') or ''
        except (KeyError, TypeError):
            pass
    if not camp_name:
        camp_name = exam.get('campName', '') or ''

    bu_name = exam.get('buName', '') or ''
    plan_name = ''
    try:
        plan_name = exam.get('planBasicInfo', {}).get('planWorkOrderDTO', {}).get('planName', '') or ''
    except (AttributeError, TypeError):
        pass
    environment = dc.get('environment', '') or ''

    # 频次控制 - 活动粒度
    activity_controls = _parse_activity_controls_raw(dc)

    # 奖品列表
    prizes = []
    for pi in exam.get('prizeBasicInfoAll', []):
        try:
            base = pi.get('prizeBaseInfoDTO', {})
            price = pi.get('priceStrategyDTO', {})
            prizes.append({
                'prize_id': base.get('prizeId', ''),
                'prize_name': base.get('prizeName', ''),
                'prize_type': base.get('prizeType', ''),
                'prize_sub_type': base.get('prizeSubType', ''),
                'max_price_raw': float(price.get('maxPrice', 0) or 0),
                'min_price_raw': float(price.get('minPrice', 0) or 0),
                'weight': base.get('weight', '1') if 'weight' in base else pi.get('weight', '1'),
                'controls': _parse_prize_controls_raw(pi),
            })
        except (KeyError, TypeError):
            continue

    # 风控字段
    ctu_level = dc.get('ctuValidateLevel')
    if not ctu_level:
        import re
        m = re.search(r'"ctuValidateLevel"\s*:\s*"([^"]*)"', ep_str)
        ctu_level = m.group(1) if m else 'NOT_FOUND'

    skip_ctu = 'missing'
    try:
        ext = exam.get('campBasicInfo', {}).get('extProperties', {}) or {}
        skip_ctu = str(ext.get('SKIP_CTU_VALIDATE', 'missing')).lower()
    except (KeyError, TypeError, AttributeError):
        pass

    # 实名/人群
    realname_raw = exam.get('realNameAuth', 0)
    try:
        realname_auth = int(realname_raw)
    except (ValueError, TypeError):
        realname_auth = 0

    urm = dc.get('userRuleModel', {}) or {}
    crowd_limit_type = urm.get('crowdLimitType', 'notLimit') or 'notLimit'

    # CP号
    camp_code = exam.get('campCode', '') or cbin.get('campCode', '')

    # 方案信息
    plan_info = exam.get('planBasicInfo', {}) or {}
    plan_wod = plan_info.get('planWorkOrderDTO', {}) or {}
    plan_id = exam.get('planId', '') or plan_info.get('planId', '')

    plan_core = {}
    plan_budget = {}
    poc = plan_info.get('planOrderContentDTO', {})
    if isinstance(poc, dict):
        plan_core = poc.get('planCoreDTO', {}) or {}
        plan_budget = poc.get('planBudgetDTO', {}) or {}

    plan_background = plan_core.get('description', '')
    display_rules = dc.get('displayRuleText', '')
    provider_type_raw = plan_budget.get('providerType', '')
    budget_amount = plan_budget.get('sendAmount', '')

    # 时间
    camp_gmt_begin = cbin.get('gmtBegin') or dc.get('gmtBegin')
    camp_gmt_end = cbin.get('gmtEnd') or dc.get('gmtEnd')
    plan_gmt_begin = plan_core.get('gmtBegin')
    pl_gmt_end = None
    cp_gmt_end = None
    try:
        pl_gmt_end = plan_wod.get('gmtEnd')
    except (AttributeError, TypeError):
        pass
    try:
        cp_gmt_end = cbin.get('gmtEnd') or dc.get('gmtEnd')
    except (AttributeError, TypeError):
        pass

    # 人员/来源
    biz_owner = cbin.get('bizOwner', '') or dc.get('bizOwner', '')
    creator = cbin.get('creator', '') or dc.get('creator', '')
    sub_biz_type_raw = dc.get('subBizType', '') or cbin.get('subBizType', '') or cbin.get('configCode', '') or dc.get('configCode', '')
    config_code = cbin.get('configCode', '') or dc.get('configCode', '')

    # 地域限制
    plan_risk = cbin.get('planRiskDTO', {}) or {}
    lbs_limit = plan_risk.get('positionLBSLimit', '')

    # 证件号为空是否默认通过
    id_card_empty = dc.get('idCardNoIsAllowEmpty', 'false')

    # 频次控制4个字段
    freq_count_limit = dc.get('countLimitModel')
    freq_count_frequency = dc.get('countFrequencyModel')
    freq_prize_count_limit = dc.get('prizeCountLimitModel')
    freq_prize_count_frequency = dc.get('prizeCountFrequencyModel')

    # 活动目标（优先级：exam顶层goal > detailContent.bizTarget）
    goals_raw = exam.get('goal', [])
    if not goals_raw:
        goals_raw = data.get('goal', [])
    if isinstance(goals_raw, str):
        try:
            import json as _json
            goals_raw = _json.loads(goals_raw)
        except Exception:
            goals_raw = [goals_raw] if goals_raw else []
    goals = goals_raw if isinstance(goals_raw, list) else [goals_raw]

    biz_target_raw = dc.get('bizTarget', [])
    if isinstance(biz_target_raw, str):
        try:
            import json as _json_biz
            biz_target_raw = _json_biz.loads(biz_target_raw)
        except Exception:
            biz_target_raw = []
    biz_targets = []
    if isinstance(biz_target_raw, list):
        for bt in biz_target_raw:
            if isinstance(bt, dict):
                val = bt.get('value', '')
                if val:
                    biz_targets.append(val)
            elif isinstance(bt, str) and bt:
                biz_targets.append(bt)

    # 单用户单日获奖次数上限
    _dim_names = {'USER_ID': '同支付宝账户', 'PHONE_NO': '同手机号', 'ID_CARD_NO': '同证件号', 'TERMINAL_ID': '同手机设备'}
    daily_prize_limit = ''
    if freq_prize_count_frequency and isinstance(freq_prize_count_frequency, dict):
        if freq_prize_count_frequency.get('limitControl') == 'limit':
            freq_limit = freq_prize_count_frequency.get('limitCount', 0)
            freq_type = freq_prize_count_frequency.get('userFrequencyLimitType', 'D')
            period_map = {'D': '每天', 'W': '每周', 'M': '每月', 'Y': '每年'}
            period = period_map.get(freq_type, '每天')
            dims = freq_prize_count_frequency.get('countFrequencyDimension', []) or []
            dim_labels = [_dim_names.get(d, d) for d in dims if d] if dims else []
            if dim_labels:
                daily_prize_limit = f'{"、".join(dim_labels)}{period}限{freq_limit}次'
            else:
                daily_prize_limit = f'{period}限{freq_limit}次'
    if not daily_prize_limit:
        daily_prize_limit = '无限制'

    return {
        'activity_id': activity_id,
        'camp_name': camp_name,
        'camp_code': camp_code,
        'bu_name': bu_name,
        'plan_name': plan_name,
        'plan_id': plan_id,
        'plan_background': plan_background,
        'display_rules': display_rules,
        'provider_type': provider_type_raw,
        'budget_amount': budget_amount,
        'camp_gmt_begin': camp_gmt_begin,
        'camp_gmt_end': camp_gmt_end,
        'plan_gmt_begin': plan_gmt_begin,
        'biz_owner': biz_owner,
        'creator': creator,
        'sub_biz_type': sub_biz_type_raw,
        'config_code': config_code,
        'lbs_limit': lbs_limit,
        'id_card_empty': id_card_empty,
        'environment': environment,
        'activity_controls': activity_controls,
        'prizes': prizes,
        'prize_count': len(prizes),
        'ctu_validate_level': ctu_level,
        'skip_ctu_validate': skip_ctu,
        'realname_auth': realname_auth,
        'crowd_limit_type': crowd_limit_type,
        'freq_count_limit': freq_count_limit,
        'freq_count_frequency': freq_count_frequency,
        'freq_prize_count_limit': freq_prize_count_limit,
        'freq_prize_count_frequency': freq_prize_count_frequency,
        'pl_gmt_end': pl_gmt_end,
        'cp_gmt_end': cp_gmt_end,
        'gmt_occur': data.get('gmtOccur'),
        'goals': goals,
        'biz_targets': biz_targets,
        'daily_prize_limit': daily_prize_limit,
        '_raw_data': data,
        '_raw_ep_str': ep_str,
    }
