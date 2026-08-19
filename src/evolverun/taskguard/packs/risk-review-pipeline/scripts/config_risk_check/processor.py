# -*- coding: utf-8 -*-
"""
配置风险校验模块 (Pipeline版)

4个子校验，全部从 pipeline data dict 读取，不再解析JSON。
1. 频次控制合理性
2. 风控规则校验
3. 未限制实名用户参加
4. 非有效PL
"""

import re
from datetime import datetime
from typing import Dict, List, Tuple

# 四个维度常量
ALL_DIMS = {'USER_ID', 'PHONE_NO', 'ID_CARD_NO', 'TERMINAL_ID'}


# ============================================================================
# 工具函数（从 data dict 读取，不解析JSON）
# ============================================================================

def _activity_has_n_same(controls, n):
    """活动粒度是否有n同"""
    return any(c['dim_count'] >= n for c in controls)


def _check_control_satisfies(controls, req_dim, req_max_count):
    """检查控制列表中是否有满足 n同+次数要求 的控制"""
    for c in controls:
        if c['dim_count'] >= req_dim:
            if c['count'] <= req_max_count:
                dim_str = '{}同'.format(c['dim_count'])
                return True, '活动{}{}{}次'.format(c['name'], dim_str, c['count'])
    return False, ''


# ============================================================================
# 豁免 & 特殊放宽
# ============================================================================

def _is_exempt_frequency(benefit_type, prize_name, camp_name, plan_name, sub_scenario):
    """频次控制豁免判断"""
    if benefit_type in ('皮肤', '皮肤装扮'):
        return True, '豁免:皮肤装扮({})'.format(benefit_type)
    keywords = ['逾期', '帮扶金', '贷后']
    if sub_scenario == '贷后':
        return True, '豁免:贷后场景'
    for kw in keywords:
        if kw in prize_name or kw in camp_name or kw in plan_name:
            return True, '豁免:贷后还款红包(含"{}")'.format(kw)
    return False, ''


def _is_special_relax(prize_name, benefit_type, scenarios, sub_scenario):
    """特殊放宽（一同即可）"""
    if '企业' in sub_scenario:
        return True
    is_credit = '消金' in scenarios or '网商' in scenarios
    if is_credit and '支用' in prize_name and benefit_type != '现金红包':
        return True
    return False


def _is_risk_control_exempt(benefit_type, prize_name, camp_name, plan_name):
    """风控校验豁免"""
    if benefit_type in ('皮肤', '皮肤装扮'):
        return True
    for kw in ['逾期', '帮扶金', '贷后']:
        if kw in prize_name or kw in camp_name or kw in plan_name:
            return True
    if '信用卡' in prize_name and '还款' in prize_name:
        return True
    if benefit_type in ('利率打折卡', '免息券', '分期收银台打折权益', '分期收银台免息权益'):
        return True
    return False


# ============================================================================
# 子模块1: 频次控制合理性校验
# ============================================================================

def check_frequency_control(data):
    """频次控制合理性校验，从 data dict 读取所有字段"""
    scenarios = data.get('scenarios', [])
    sub_scenario = data.get('sub_scenario', '')
    prize_values = data.get('prize_values', {})
    camp_name = data.get('camp_name', '') or ''
    plan_name = data.get('plan_name', '') or ''
    act_controls = data.get('activity_controls', [])
    prizes = data.get('prizes', [])
    is_ws = '网商' in scenarios

    if not prize_values:
        return {'pass': True, 'reason': '无奖品信息，跳过', 'details': []}

    # 建立奖品 controls 映射
    prize_controls_map = {}
    for p in prizes:
        prize_id = p.get('prize_id', '')
        if prize_id:
            prize_controls_map[prize_id] = p.get('controls', {'dim_count': 0, 'count': 0})

    details = []
    all_pass = True

    for prize_id, pv in prize_values.items():
        name = pv.get('prize_name', '')
        btype = pv.get('benefit_type', '')
        val = pv.get('true_value', 0) or 0

        exempt, exempt_reason = _is_exempt_frequency(btype, name, camp_name, plan_name, sub_scenario)
        if exempt:
            details.append({'prize_id': prize_id, 'prize_name': name, 'pass': True, 'reason': exempt_reason})
            continue

        if val <= 0:
            details.append({'prize_id': prize_id, 'prize_name': name, 'pass': True, 'reason': '无现金价值({}元)'.format(val)})
            continue

        special = _is_special_relax(name, btype, scenarios, sub_scenario)

        if val >= 1:
            req_dim = 1 if special else 4
            req_max = 1  # 终身限制1次
            desc = '>=1元({:.4f})'.format(val)
        elif val >= 0.1:
            req_dim = 1 if special else 4
            req_max = 10 if is_ws else 100
            desc = '>=0.1元({:.4f})'.format(val)
        else:
            req_dim = 1
            req_max = 10 if is_ws else 100
            desc = '>0元({:.4f})'.format(val)

        act_ok, act_reason = _check_control_satisfies(act_controls, req_dim, req_max)
        if act_ok:
            details.append({'prize_id': prize_id, 'prize_name': name, 'pass': True, 'reason': act_reason})
            continue

        pz_ok = False
        pz_reason = ''
        pc = prize_controls_map.get(prize_id, {'dim_count': 0, 'count': 0})
        if pc['dim_count'] >= req_dim:
            if pc['count'] <= req_max:
                pz_ok = True
                pz_reason = '奖品粒度{}同{}次'.format(pc['dim_count'], pc['count'])

        if pz_ok:
            details.append({'prize_id': prize_id, 'prize_name': name, 'pass': True, 'reason': pz_reason})
        else:
            all_pass = False
            need_dim = '{}同'.format(req_dim)
            need_cnt = '<={}次'.format(req_max)
            parts = []
            for c in act_controls:
                parts.append('活动{}{}同{}次'.format(c['name'], c['dim_count'], c['count']))
            if pc['dim_count'] > 0:
                parts.append('奖品{}同{}次'.format(pc['dim_count'], pc['count']))
            current = '; '.join(parts) if parts else '无任何限制'
            reason = '{}：需{}+{}，当前{}'.format(desc, need_dim, need_cnt, current)
            details.append({'prize_id': prize_id, 'prize_name': name, 'pass': False, 'reason': reason})

    failed = [d for d in details if not d['pass']]
    if all_pass:
        return {'pass': True, 'reason': '所有奖品频次控制合理', 'details': details}
    return {'pass': False, 'reason': '{}个奖品频次控制不合理'.format(len(failed)), 'details': details}


# ============================================================================
# 子模块2: 风控规则校验
# ============================================================================

def check_risk_control(data):
    """风控规则校验"""
    ctu_level = data.get('ctu_validate_level', 'NOT_FOUND') or 'NOT_FOUND'
    skip_ctu = data.get('skip_ctu_validate', 'missing') or 'missing'
    prize_values = data.get('prize_values', {})
    camp_name = data.get('camp_name', '') or ''
    plan_name = data.get('plan_name', '') or ''

    risk_desc = 'ctuLevel={},SKIP_CTU={}'.format(ctu_level, skip_ctu)

    if skip_ctu == 'true':
        pass  # 不通过，继续走豁免检查
    elif ctu_level == 'REAL_TIME':
        return {'pass': True, 'reason': '已咨询实时风控({})'.format(risk_desc)}
    elif ctu_level == 'NOT_FOUND' and skip_ctu != 'true':
        return {'pass': True, 'reason': '默认咨询风控({})'.format(risk_desc)}

    if not prize_values:
        return {'pass': False, 'reason': '未咨询实时风控({})，且无奖品信息'.format(risk_desc)}

    non_exempt = []
    for prize_id, pv in prize_values.items():
        if not _is_risk_control_exempt(pv.get('benefit_type', ''), pv.get('prize_name', ''),
                                       camp_name, plan_name):
            non_exempt.append('{}({})'.format(pv.get('prize_name', ''), pv.get('benefit_type', '')))

    if not non_exempt:
        return {'pass': True, 'reason': '未咨询实时风控({})，但全部奖品为豁免类型'.format(risk_desc)}
    return {'pass': False, 'reason': '未咨询实时风控({})，存在非豁免奖品: {}'.format(risk_desc, ', '.join(non_exempt[:3]))}


# ============================================================================
# 子模块3: 未限制实名用户参加
# ============================================================================

def check_realname_limit(data):
    """未限制实名用户参加校验"""
    realname_auth = data.get('realname_auth', 0)
    crowd_limit_type = data.get('crowd_limit_type', 'notLimit') or 'notLimit'
    act_controls = data.get('activity_controls', [])
    prizes = data.get('prizes', [])

    if realname_auth != 0:
        return {'pass': True, 'reason': '已要求实名(realNameAuth={})'.format(realname_auth)}

    if crowd_limit_type and crowd_limit_type != 'notLimit':
        return {'pass': True, 'reason': '已限制人群(crowdLimitType={})'.format(crowd_limit_type)}

    if _activity_has_n_same(act_controls, 4):
        return {'pass': True, 'reason': '未要求实名和人群限制，但活动粒度有四同'}

    # 检查所有奖品粒度是否都有四同
    if prizes:
        all_prize_4same = all(p.get('controls', {}).get('dim_count', 0) >= 4 for p in prizes)
        if all_prize_4same:
            return {'pass': True, 'reason': '未要求实名和人群限制，但全部奖品有四同'}

    return {'pass': False, 'reason': '未限制实名用户参加：未要求实名、未限制人群、无四同限制'}


# ============================================================================
# 子模块4: 非有效PL
# ============================================================================

def check_valid_pl(data):
    """非有效PL校验"""
    pl_gmt_end = data.get('pl_gmt_end')
    cp_gmt_end = data.get('cp_gmt_end')

    # ==========================================================================
    # ⚠️ TEMPORARY_OFFLINE_TEST_ONLY - 临时线下测试逻辑，上线前必须删除
    # ���下测试时用 gmtOccur（事件发生时间）代替当前时间判断PL是否过期，
    # 因为测试数据是历史活动，用当前时间会导致所有PL都过期。
    # 上线时应恢复为: now_ms = int(datetime.now().timestamp() * 1000)
    # ==========================================================================
    gmt_occur = data.get('gmt_occur')
    if gmt_occur is not None:
        try:
            now_ms = int(gmt_occur)  # TEMPORARY_OFFLINE_TEST_ONLY
        except (ValueError, TypeError):
            now_ms = int(datetime.now().timestamp() * 1000)
    else:
        now_ms = int(datetime.now().timestamp() * 1000)
    # ==================== END TEMPORARY_OFFLINE_TEST_ONLY ====================
    problems = []

    if pl_gmt_end is not None:
        try:
            pl_end_ms = int(pl_gmt_end)
            if pl_end_ms < now_ms:
                dt = datetime.fromtimestamp(pl_end_ms / 1000).strftime('%Y-%m-%d')
                problems.append('预算已过期(PL结束:{})'.format(dt))
        except (ValueError, TypeError):
            pass

    if pl_gmt_end is not None and cp_gmt_end is not None:
        try:
            if int(cp_gmt_end) > int(pl_gmt_end) + 60000:
                pl_dt = datetime.fromtimestamp(int(pl_gmt_end) / 1000).strftime('%Y-%m-%d')
                cp_dt = datetime.fromtimestamp(int(cp_gmt_end) / 1000).strftime('%Y-%m-%d')
                problems.append('CP超PL(CP结束:{},PL结束:{})'.format(cp_dt, pl_dt))
        except (ValueError, TypeError):
            pass

    if problems:
        return {'pass': False, 'reason': '; '.join(problems)}
    return {'pass': True, 'reason': 'PL有效'}


# ============================================================================
# Pipeline enrich 接口
# ============================================================================

def enrich(data):
    """
    Pipeline enrichment: 运行4项配置风险校验。

    添加字段:
    - config_checks: dict (4项)
    - has_config_risk: bool
    - config_risk_reasons: list[str]
    """
    if data is None:
        fail = {'pass': False, 'reason': '数据为空'}
        data = {'config_checks': {
            'frequency_control': fail, 'risk_control': fail,
            'realname_limit': fail, 'valid_pl': fail,
        }, 'has_config_risk': True, 'config_risk_reasons': ['数据为空，无法判断']}
        return data

    if data.get('_parse_failed'):
        fail = {'pass': False, 'reason': '解析失败，无法判断风险'}
        data['config_checks'] = {
            'frequency_control': fail, 'risk_control': fail,
            'realname_limit': fail, 'valid_pl': fail,
        }
        data['has_config_risk'] = True
        data['config_risk_reasons'] = ['数据解析失败，无法判断配置风险']
        return data

    checks = {
        'frequency_control': check_frequency_control(data),
        'risk_control': check_risk_control(data),
        'realname_limit': check_realname_limit(data),
        'valid_pl': check_valid_pl(data),
    }

    config_risk_reasons = []
    for name, chk in checks.items():
        if not chk.get('pass', True):
            config_risk_reasons.append('{}: {}'.format(name, chk.get('reason', '')))

    data['config_checks'] = checks
    data['has_config_risk'] = len(config_risk_reasons) > 0
    data['config_risk_reasons'] = config_risk_reasons
    return data
