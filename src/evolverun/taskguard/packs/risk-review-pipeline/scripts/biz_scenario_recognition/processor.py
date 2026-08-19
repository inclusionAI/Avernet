"""
业务场景识别模块

根据业务线(buName)、方案名称(planName)、活动名称(campName)中的关键词，
识别活动所属的业务场景：网商、消金、财富、保险。

另有特殊子场景（独立维度）：贷后、企业（BUname判断，与主场景并行存在）。

判断逻辑：
- 特殊子场景：BUname 包含"贷后"/"企业"/"B端" → 标记特殊子场景（不影响主场景识别）
- 主key优先级：buName > planName > campName
- 每个主key内，第一优先级关键词 > 第二优先级关键词
- 逐级判断：若某级key命中恰好1个场景，直接返回；命中0或多个则继续下一级
- 全部走完仍为多个，冲突消解：网商 > 财富 > 保险（消金不参与冲突）
"""

from typing import List, Tuple

# 特殊子场景关键词（独立维度，与主场景并行）
SPECIAL_SUB_SCENARIO_KEYWORDS = {
    '贷后': ['贷后'],
    '企业': ['企业', 'B端'],
}

# 多场景冲突消解优先级（值越小优先级越高，消金不参与冲突）
SCENARIO_PRIORITY = {
    '网商': 1,
    '财富': 2,
    '保险': 3,
}

# 分优先级的场景关键词配置
SCENARIO_KEYWORDS_P1 = {
    '网商': [
        'R88', '网商', '网商贷', '小微业务',
        '余利宝', '稳利宝', '周利宝', '月利宝', '增利宝',
        '生意卡', '生意金卡', '数币', '数字人民币',
    ],
    '消金': [
        '信用卡', '花呗', '借呗', '信用购', '宝藏卡', '宝藏特权',
        '消金', '消费金融', '车生活',
    ],
    '财富': [
        '蚂蚁财富', '大稳健', '余额宝', '黑卡', '蚂小财', '黄金票', '小荷包',
        '高端理财',
    ],
    '保险': [
        '保险', '蚂蚁保', '安心豆', '险',
    ],
}

SCENARIO_KEYWORDS_P2 = {
    '网商': [
        '蚂蚁星河', '福利金', '发发日', '福利日',
        '淘宝贷款', '立享卡', '飞升卡', '云资金', '理财',
    ],
    '消金': [
        '还款',
    ],
    '财富': [
        '蚂蚁基金', '股票', '养老金', '财富', '基金', '理财',
        '大权益', '帮你投', '财保',
    ],
    '保险': [
        '财保',
    ],
}


def _match_scenarios_in_text(text: str, keyword_map: dict) -> Tuple[List[str], List[str]]:
    """在给定文本中匹配场景，返回 (命中场景列表, 匹配详情列表)"""
    matched = []
    details = []
    for scenario, keywords in keyword_map.items():
        for kw in keywords:
            if kw in text:
                matched.append(scenario)
                details.append(f"{scenario}('{kw}')")
                break
    return matched, details


def _detect_special_sub_scenario(bu_name: str) -> Tuple[str, str]:
    """检测特殊子场景，返回 (特殊子场景名, 匹配说明)，未命中返回 ('', '')"""
    if not bu_name:
        return '', ''
    for scenario, keywords in SPECIAL_SUB_SCENARIO_KEYWORDS.items():
        for kw in keywords:
            if kw in bu_name:
                return scenario, f"特殊子场景: {scenario}('{kw}')@业务线"
    return '', ''


def _resolve_conflicts(scenarios: List[str]) -> List[str]:
    """
    多场景冲突消解：网商 > 财富 > 保险（消金不参与冲突）

    规则：
    - 消金始终保留，不参与冲突消解
    - 网商、财富、保险之间有冲突时，只保留优先级最高的
    """
    non_conflict = [s for s in scenarios if s not in SCENARIO_PRIORITY]  # 消金等
    conflict = [s for s in scenarios if s in SCENARIO_PRIORITY]

    if len(conflict) <= 1:
        return non_conflict + conflict

    # 取优先级最高的（数值最小）
    winner = min(conflict, key=lambda s: SCENARIO_PRIORITY[s])
    return non_conflict + [winner]


def identify_scenario(bu_name: str, camp_name: str, plan_name: str) -> Tuple[List[str], str, str]:
    """
    识别活动所属的业务场景

    判断流程：
    0. 特殊子场景检测：BUname 包含"贷后"/"企业"/"B端" → 标记特殊子场景（独立维度）
    1. 理财+财富组合命中 → 财富P1（优先判断）
    2. buName P1 → 恰好1个场景 → 返回
    3. buName P2 → 恰好1个场景 → 返回
    4. planName P1 → 恰好1个场景 → 返回
    5. planName P2 → 恰好1个场景 → 返回
    6. campName P1 → 恰好1个场景 → 返回
    7. campName P2 → 恰好1个场景 → 返回
    8. 全部走完仍为多个 → 冲突消解（网商>财富>保险）

    Args:
        bu_name: 业务线名称
        camp_name: 活动名称
        plan_name: 方案名称

    Returns:
        (主场景列表, 特殊子场景, 匹配说明)
        示例: (['财富'], '企业', "财富('蚂蚁财富')@业务线 | 特殊子场景: 企业('企业')@业务线")
    """
    bu_name = bu_name or ''
    plan_name = plan_name or ''
    camp_name = camp_name or ''

    # 特殊子场景检测（独立维度，不影响主场景识别）
    special_sub, special_desc = _detect_special_sub_scenario(bu_name)

    # 同时命中"理财"和"财富"两个关键字 → 财富场景（P1级别）
    for source_name, text in [('业务线', bu_name), ('方案名称', plan_name), ('活动名称', camp_name)]:
        if text and '理财' in text and '财富' in text:
            main_desc = f"财富('理财'+'财富'组合)@{source_name}"
            full_desc = f"{main_desc} | {special_desc}" if special_desc else main_desc
            return ['财富'], special_sub, full_desc

    # 按主key优先级 × 关键词优先级，共6轮判断
    checks = [
        ('业务线', bu_name, SCENARIO_KEYWORDS_P1),
        ('业务线', bu_name, SCENARIO_KEYWORDS_P2),
        ('方案名称', plan_name, SCENARIO_KEYWORDS_P1),
        ('方案名称', plan_name, SCENARIO_KEYWORDS_P2),
        ('活动名称', camp_name, SCENARIO_KEYWORDS_P1),
        ('活动名称', camp_name, SCENARIO_KEYWORDS_P2),
    ]

    all_matched = []
    all_details = []

    for source_name, text, kw_map in checks:
        if not text:
            continue
        matched, details = _match_scenarios_in_text(text, kw_map)
        if matched:
            tagged_details = [f"{d}@{source_name}" for d in details]
            all_matched.extend(matched)
            all_details.extend(tagged_details)
            if len(matched) == 1:
                main_desc = '; '.join(tagged_details)
                full_desc = f"{main_desc} | {special_desc}" if special_desc else main_desc
                return matched, special_sub, full_desc

    # 全部走完，取累计去重后做冲突消解
    if all_matched:
        seen = []
        for s in all_matched:
            if s not in seen:
                seen.append(s)
        resolved = _resolve_conflicts(seen)
        detail_str = '; '.join(all_details)
        if len(resolved) < len(seen):
            detail_str += f" → 冲突消解: {seen} → {resolved}"
        full_desc = f"{detail_str} | {special_desc}" if special_desc else detail_str
        return resolved, special_sub, full_desc

    full_desc = f"未匹配任何场景 | {special_desc}" if special_desc else '未匹配任何场景'
    return [], special_sub, full_desc


# ============================================================================
# Pipeline enrich 接口
# ============================================================================

def enrich(data):
    """
    Pipeline enrichment: 识别业务场景，写入 data dict。

    添加字段:
    - scenarios: list[str]  主场景列表
    - sub_scenario: str     特殊子场景
    - scenario_desc: str    匹配说明
    """
    if data is None or data.get('_parse_failed'):
        data['scenarios'] = []
        data['sub_scenario'] = ''
        data['scenario_desc'] = '解析失败，跳过'
        return data

    bu_name = data.get('bu_name', '') or ''
    camp_name = data.get('camp_name', '') or ''
    plan_name = data.get('plan_name', '') or ''

    scenarios, sub_scenario, scenario_desc = identify_scenario(bu_name, camp_name, plan_name)

    data['scenarios'] = scenarios
    data['sub_scenario'] = sub_scenario
    data['scenario_desc'] = scenario_desc
    return data
