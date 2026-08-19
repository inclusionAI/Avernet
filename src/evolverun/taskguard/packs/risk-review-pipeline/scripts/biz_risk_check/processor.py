# -*- coding: utf-8 -*-
"""
业务风险校验模块 (Pipeline版)

基于玩法、权益、业务场景三个维度进行业务风险识别。
全部从 pipeline data dict 读取，不再解析JSON。
"""

from typing import Dict, List

import re

# 机器作弊类玩法
MACHINE_CHEAT_GAMEPLAYS = {'秒杀类', '签到打卡类', '浏览类', '游戏类', '红包雨'}

# 拉新相关关键词
LAXIN_KEYWORDS = ['拉新', '开户', '设首', '首登', '首签', '首购', '首开']

# 设首关键词
SHESHOU_KEYWORDS = ['设首', '设为首选', '首选支付']


# ============================================================================
# 工具函数
# ============================================================================

def _activity_has_n_same(controls, n):
    return any(c['dim_count'] >= n for c in controls)


# ============================================================================
# 一、基于玩法的风险识别
# ============================================================================

def check_machine_cheat_risk(data):
    """机器作弊类风险"""
    gameplays = data.get('gameplay_names', [])
    hit = [g for g in gameplays if g in MACHINE_CHEAT_GAMEPLAYS]
    if not hit:
        return {'pass': True, 'reason': '非机器作弊类玩法', 'hit_gameplays': []}

    # 简化：使用 prize_values 估算 activity_expected_value
    # 实际需要更精确计算，这里先用总价值替代
    prize_values = data.get('prize_values', {})
    total_value = sum(pv.get('true_value', 0) or 0 for pv in prize_values.values())

    act_controls = data.get('activity_controls', [])

    if total_value < 0.1:
        if _activity_has_n_same(act_controls, 4):
            return {
                'pass': True,
                'reason': '机器作弊类玩法({})，但CP价值<0.1元且有四同，建议配置防控模块'.format(','.join(hit)),
                'hit_gameplays': hit,
                'need_defense_module': True,
            }

    return {
        'pass': False,
        'reason': '机器作弊类玩法({})，CP价值={:.4f}元，需人工接入蓝鉴防控'.format(','.join(hit), total_value),
        'hit_gameplays': hit,
    }


def check_laxin_risk(data):
    """低质拉新风险"""
    camp_name = data.get('camp_name', '') or ''
    plan_name = data.get('plan_name', '') or ''
    prize_names = [p.get('prize_name', '') for p in data.get('prizes', [])]
    act_controls = data.get('activity_controls', [])
    prizes = data.get('prizes', [])
    crowd_limit_type = data.get('crowd_limit_type', 'notLimit') or 'notLimit'

    all_text = '{} {} {}'.format(camp_name, plan_name, ' '.join(prize_names))
    hit_kw = ''
    for kw in LAXIN_KEYWORDS:
        if kw in all_text:
            hit_kw = kw
            break

    # 关键词未命中时，大模型兜底判断
    if not hit_kw:
        hit_kw = _llm_check_laxin(camp_name, plan_name, prize_names)
    if not hit_kw:
        return {'pass': True, 'reason': '非拉新类活动'}

    has_crowd = crowd_limit_type and crowd_limit_type != 'notLimit'
    has_four_same = _activity_has_n_same(act_controls, 4)

    if not has_four_same and prizes:
        all_prize_4same = all(p.get('controls', {}).get('dim_count', 0) >= 4 for p in prizes)
        has_four_same = all_prize_4same

    problems = []
    if not has_crowd:
        problems.append('无人群限制')
    if not has_four_same:
        problems.append('无四同限制')

    if problems:
        return {'pass': False, 'reason': '拉新活动(含"{}")存在低质拉新风险: {}'.format(hit_kw, ', '.join(problems))}
    return {'pass': True, 'reason': '拉新活动(含"{}")，已配置人群限制和四同'.format(hit_kw)}


def check_renchuanren_risk(data):
    """
    人传人风险

    - 被邀请人奖品必须四同+有次数限制
    - 通过奖品名/活动名区分邀请人/被邀请人
    - 被邀请人准入需有人群限制门槛
    """
    gameplays = data.get('gameplay_names', [])
    if '线上人传人' not in gameplays and '线下人传人' not in gameplays:
        return {'pass': True, 'reason': '非人传人玩法'}

    act_controls = data.get('activity_controls', [])
    crowd_limit_type = data.get('crowd_limit_type', 'notLimit') or 'notLimit'
    prizes = data.get('prizes', [])

    # 区分被邀请人奖品
    invited_prizes = []
    for p in prizes:
        pname = p.get('prize_name', '')
        if '被邀请' in pname or '被分享' in pname:
            invited_prizes.append(p)

    # 如果能区分，检查被邀请人奖品的四同+有限制
    problems = []
    if invited_prizes:
        for p in invited_prizes:
            pc = p.get('controls', {})
            pname = p.get('prize_name', '')
            if pc.get('dim_count', 0) < 4 or pc.get('count', 0) <= 0:
                problems.append('被邀请人奖品"{}"未配置四同限制({}同{}次)'.format(
                    pname, pc.get('dim_count', 0), pc.get('count', 0)))
    else:
        # 无法区分，整体检查活动粒度四同
        has_four_same = _activity_has_n_same(act_controls, 4)
        if not has_four_same:
            # 检查所有奖品粒度
            all_4same = all(p.get('controls', {}).get('dim_count', 0) >= 4 for p in prizes) if prizes else False
            if not all_4same:
                problems.append('未限制四同（被邀请人可重复助力）')

    has_crowd = crowd_limit_type and crowd_limit_type != 'notLimit'
    if not has_crowd:
        problems.append('无准入门槛（众包风险高）')

    if problems:
        return {'pass': False, 'reason': '人传人玩法风险: {}'.format(', '.join(problems))}
    return {'pass': True, 'reason': '人传人玩法，已配置四同和准入门槛'}


# ============================================================================
# 二、基于权益的风险识别
# ============================================================================

def check_experience_fund_risk(data):
    """体验金收益波动风险"""
    prize_values = data.get('prize_values', {})
    problems = []

    for prize_id, pv in prize_values.items():
        name = pv.get('prize_name', '')
        btype = pv.get('benefit_type', '')
        max_price = pv.get('max_price_raw', 0) or 0

        if 'ETF' in name:
            continue

        is_experience = '体验金' in name or '体验金' in btype
        if not is_experience:
            continue

        face_value = max_price * 0.01 if max_price else 0
        is_high_risk = '股票' in name or '增利宝' in name
        if is_high_risk and face_value > 1000:
            problems.append('{}: 面值{:.0f}元>1000元'.format(name, face_value))

    if problems:
        return {'pass': False, 'reason': '体验金收益波动风险: {}'.format('; '.join(problems))}
    return {'pass': True, 'reason': '无体验金收益波动风险'}


def _extract_threshold(prize_name):
    """
    从奖品名中提取门槛金额

    匹配模式：满1000减N / 满1000元 / 1000元门槛
    无法识别时返回 0
    """
    patterns = [
        r'满(\d+(?:\.\d+)?)\s*[元减]',
        r'(\d+(?:\.\d+)?)\s*元门槛',
        r'门槛(\d+(?:\.\d+)?)',
    ]
    for pat in patterns:
        m = re.search(pat, prize_name)
        if m:
            return float(m.group(1))
    return 0


def check_sheshou_risk(data):
    """
    设首红包权益过高风险

    - 现金红包：单个奖品 true_value > 1元 → 有风险
    - 分期红包：true_value > 门槛/1000×20 → 有风险（门槛=0时当作无风险）
    """
    camp_name = data.get('camp_name', '') or ''
    plan_name = data.get('plan_name', '') or ''
    prize_values = data.get('prize_values', {})

    all_text = '{} {}'.format(camp_name, plan_name)
    is_sheshou = any(kw in all_text for kw in SHESHOU_KEYWORDS)

    if not is_sheshou:
        return {'pass': True, 'reason': '非设首类活动'}

    problems = []
    for prize_id, pv in prize_values.items():
        btype = pv.get('benefit_type', '')
        val = pv.get('true_value', 0) or 0
        name = pv.get('prize_name', '')

        # 现金红包类
        if btype in ('现金红包', '支付红包', '场景红包', '还款红包'):
            if val > 1:
                problems.append('{}({}): {:.2f}元>1元'.format(name, btype, val))

        # 分期红包类
        elif '分期' in btype or '分期' in name:
            threshold = _extract_threshold(name)
            if threshold > 0:
                max_val = threshold / 1000 * 20
                if val > max_val:
                    problems.append('{}(分期,门槛{:.0f}元): {:.2f}元>{:.2f}元'.format(name, threshold, val, max_val))
            # 门槛=0时当作无风险

    if problems:
        return {'pass': False, 'reason': '设首红包过高: {}'.format('; '.join(problems))}
    return {'pass': True, 'reason': '设首活动红包力度合理'}


# ============================================================================
# 三、基于业务场景的风险识别
# ============================================================================

def check_licai_risk(data):
    """
    理财场景风险

    - 股票/增利宝体验金面值>1000元 → 有风险
    - 其他体验金面值>50000元 → 有风险
    - 货币基金申购红包：true_value > 门槛/10000×2 → 有风险（门槛从奖品名提取）
    """
    scenarios = data.get('scenarios', [])
    prize_values = data.get('prize_values', {})

    if '财富' not in scenarios:
        return {'pass': True, 'reason': '非理财场景'}

    problems = []
    for prize_id, pv in prize_values.items():
        name = pv.get('prize_name', '')
        max_price = pv.get('max_price_raw', 0) or 0
        face_value = max_price * 0.01 if max_price else 0
        val = pv.get('true_value', 0) or 0
        threshold = pv.get('threshold', 0) or 0

        # 体验金面额限制
        if '体验金' in name and 'ETF' not in name:
            if ('股票' in name or '增利宝' in name) and face_value > 1000:
                problems.append('{}: 面值{:.0f}元>1000元限额'.format(name, face_value))
            elif face_value > 50000:
                problems.append('{}: 面值{:.0f}元>50000元限额'.format(name, face_value))

        # 货币基金申购红包力度：每申购10000元，红包不超过2元
        if threshold > 0 and ('余额宝' in name or '余利宝' in name):
            max_val = threshold / 10000 * 2
            if val > max_val:
                problems.append('{}: 门槛{:.0f}元,红包{:.2f}元>{:.2f}元限额'.format(name, threshold, val, max_val))

    if problems:
        return {'pass': False, 'reason': '理财场景风险: {}'.format('; '.join(problems))}
    return {'pass': True, 'reason': '理财场景权益力度合理'}


def check_dapro_risk(data):
    """
    大型活动风险

    命中大促关键词或PZ数>20 → 需人工复核
    """
    is_dapro = data.get('is_dapro', False)
    dapro_reason = data.get('dapro_reason', '')

    if not is_dapro:
        return {'pass': True, 'reason': '非大型活动'}
    return {'pass': False, 'reason': '大型活动风险({}), 需人工复核'.format(dapro_reason)}


# ============================================================================
# 大模型辅助
# ============================================================================

_laxin_cache = {}


def _llm_check_laxin(camp_name, plan_name, prize_names):
    """
    大模型判断是否为拉新活动

    返回命中的拉新关键描述，未命中返回空字符串
    """
    cache_key = camp_name
    if cache_key in _laxin_cache:
        return _laxin_cache[cache_key]

    try:
        import sys, os
        _project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..')
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        from glm_client import query_glm

        prizes_str = '、'.join(prize_names[:5]) if prize_names else '无'
        prompt = """判断以下营销活动是否为"拉新类"活动。
拉新类活动是指：吸引新用户首次使用某服务、首次开通、首次申购、首次支付、设首等场景。

活动名称：{}
方案名称：{}
奖品名称：{}

是否为拉新类活动？只回答"是"或"否"。""".format(camp_name, plan_name, prizes_str)

        result = query_glm(prompt, model='glm-5', temperature=0.0, max_tokens=10)
        result = result.strip()

        if '是' in result and '不是' not in result and '否' not in result:
            _laxin_cache[cache_key] = '大模型判断为拉新'
            return '大模型判断为拉新'
    except Exception:
        pass

    _laxin_cache[cache_key] = ''
    return ''


# ============================================================================
# Pipeline enrich 接口
# ============================================================================

def enrich(data):
    """
    Pipeline enrichment: 运行业务风险校验。

    添加字段:
    - biz_checks: dict
    - has_biz_risk: bool
    - biz_risk_reasons: list[str]
    """
    if data is None or data.get('_parse_failed'):
        skip = {'pass': True, 'reason': '解析失败，跳过'}
        data['biz_checks'] = {k: skip for k in ['machine_cheat', 'laxin', 'renchuanren',
                                                  'experience_fund', 'sheshou', 'licai', 'dapro']}
        data['has_biz_risk'] = False
        data['biz_risk_reasons'] = []
        return data

    checks = {
        'machine_cheat': check_machine_cheat_risk(data),
        'laxin': check_laxin_risk(data),
        'renchuanren': check_renchuanren_risk(data),
        'experience_fund': check_experience_fund_risk(data),
        'sheshou': check_sheshou_risk(data),
        'licai': check_licai_risk(data),
        'dapro': check_dapro_risk(data),
    }

    biz_risk_reasons = [c['reason'] for c in checks.values() if not c['pass']]

    data['biz_checks'] = checks
    data['has_biz_risk'] = len(biz_risk_reasons) > 0
    data['biz_risk_reasons'] = biz_risk_reasons
    return data
