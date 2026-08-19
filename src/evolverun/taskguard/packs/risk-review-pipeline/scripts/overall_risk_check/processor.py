# -*- coding: utf-8 -*-
"""
综合风险判断模块 (Pipeline版 - 编排器)

run_pipeline(row) 按顺序调用各模块的 enrich() 函数，
最终汇总输出综合风险判断。
"""

import os
import sys
from typing import Dict

# 确保 skills 目录在 sys.path 中
# 沙箱环境可能部署在 skills-local/ 或 skills/ 下，兄弟 skill 可能在另一个目录
_skills_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SANDBOX_WORKSPACE = "/home/admin/.openclaw/workspace"
_skills_dirs = [_skills_dir]
if os.path.isdir(_skills_dir):
    _parent = os.path.dirname(_skills_dir)
    _basename = os.path.basename(_skills_dir.rstrip('/'))
    if _basename == 'skills-local':
        _sibling = os.path.join(_parent, 'skills')
    elif _basename == 'skills':
        _sibling = os.path.join(_parent, 'skills-local')
    else:
        _sibling = None
    if _sibling and os.path.isdir(_sibling) and _sibling not in _skills_dirs:
        _skills_dirs.append(_sibling)
for _std in [os.path.join(_SANDBOX_WORKSPACE, 'skills-local'),
             os.path.join(_SANDBOX_WORKSPACE, 'skills')]:
    if os.path.isdir(_std) and _std not in _skills_dirs:
        _skills_dirs.append(_std)
for _sd in _skills_dirs:
    if _sd not in sys.path:
        sys.path.insert(0, _sd)

from data_preprocessing.processor import preprocess_row
from biz_scenario_recognition.processor import enrich as enrich_scenario
from prize_value_recognition.processor import enrich as enrich_prize_value
from gameplay_recognition.processor import enrich as enrich_gameplay
from config_risk_check.processor import enrich as enrich_config_risk
from biz_risk_check.processor import enrich as enrich_biz_risk

# 测试/预发环境关键词
TEST_KEYWORDS = ['测试', '预发', '验证', 'test', '压测']


def _is_test_activity(data):
    """
    判断是否为测试/预发活动

    仅通过 environment 字段判断，不通过活动名称关键词判断。

    Returns:
        (is_test: bool, reason: str)
    """
    environment = data.get('environment', '') or ''
    if environment in ('PRE_ENV', 'PRERELEASE_ENV'):
        return True, 'environment={}'.format(environment)

    return False, ''


def run_pipeline(row):
    """
    Pipeline 主入口：对单行数据运行完整的风险评审流程。

    Args:
        row: dict, 包含 event_property_update 或 event_property 字段

    Returns:
        dict with keys:
        - activity_id, camp_name
        - is_test, test_reason
        - has_risk, risk_summary
        - has_config_risk, config_risk_reasons, config_checks
        - has_biz_risk, biz_risk_reasons, biz_checks
        - gameplay_names, is_dapro
        - scenarios, sub_scenario
        - prize_values
    """
    # Step 1: 获取原始字符串（兼容 event_property_update 和 event_property）
    ep_str = row.get('event_property_update') or row.get('event_property') or ''
    if not ep_str or str(ep_str) == 'nan':
        ep_str = row.get('event_property') or row.get('event_property_update') or ''
    ep_str = str(ep_str) if ep_str and str(ep_str) != 'nan' else ''

    # Step 2: 预处理
    data = preprocess_row(ep_str)

    # 解析失败
    if data.get('_parse_failed'):
        skip = {'pass': True, 'reason': '解析失败，跳过'}
        data.update({
            'has_risk': False, 'risk_summary': 'JSON解析失败',
            'has_config_risk': False, 'config_risk_reasons': [],
            'config_checks': {k: skip for k in ['frequency_control', 'risk_control', 'realname_limit', 'valid_pl']},
            'has_biz_risk': False, 'biz_risk_reasons': [],
            'biz_checks': {},
            'gameplay_names': [], 'is_dapro': False,
            'scenarios': [], 'sub_scenario': '',
            'prize_values': {},
        })
        return data

    # Step 3: 测试/预发环境检查
    is_test, test_reason = _is_test_activity(data)
    if is_test:
        skip = {'pass': True, 'reason': '测试/预发活动，跳过({})'.format(test_reason)}
        data.update({
            'is_test': True, 'test_reason': test_reason,
            'has_risk': False, 'risk_summary': '跳过评审({})'.format(test_reason),
            'has_config_risk': False, 'config_risk_reasons': [],
            'config_checks': {k: skip for k in ['frequency_control', 'risk_control', 'realname_limit', 'valid_pl']},
            'has_biz_risk': False, 'biz_risk_reasons': [],
            'biz_checks': {},
            'gameplay_names': [], 'is_dapro': False,
            'scenarios': [], 'sub_scenario': '',
            'prize_values': {},
        })
        return data

    # Step 4: Pipeline enrich 链
    data = enrich_scenario(data)       # scenarios, sub_scenario, scenario_desc
    data = enrich_prize_value(data)    # prize_values, prizes[].benefit_type/true_value/...
    data = enrich_gameplay(data)       # gameplay_names, gameplays, is_dapro, dapro_reason
    data = enrich_config_risk(data)    # config_checks, has_config_risk, config_risk_reasons
    data = enrich_biz_risk(data)       # biz_checks, has_biz_risk, biz_risk_reasons

    # Step 5: 汇总
    has_config_risk = data.get('has_config_risk', False)
    has_biz_risk = data.get('has_biz_risk', False)
    has_risk = has_config_risk or has_biz_risk

    config_risk_reasons = data.get('config_risk_reasons', [])
    biz_risk_reasons = data.get('biz_risk_reasons', [])

    if not has_risk:
        risk_summary = '无风险'
    elif has_config_risk and has_biz_risk:
        risk_summary = '配置风险({}项) + 业务风险({}项)'.format(len(config_risk_reasons), len(biz_risk_reasons))
    elif has_config_risk:
        risk_summary = '配置风险({}项)'.format(len(config_risk_reasons))
    else:
        risk_summary = '业务风险({}项)'.format(len(biz_risk_reasons))

    # 汇总字段写入 data，直接返回完整 data
    data['has_risk'] = has_risk
    data['risk_summary'] = risk_summary

    return data


# 保留旧接口兼容
run_overall_risk_check = run_pipeline


# ==============================================================================
# to_final_res: 将 pipeline 输出转为线上 final_res 格式
# 基于「数金一页纸内容」文档需求
# ==============================================================================

import re as _re

# 维度中文映射
_DIM_MAP = {
    'USER_ID': '同支付宝账户',
    'PHONE_NO': '同手机号',
    'ID_CARD_NO': '同证件号',
    'TERMINAL_ID': '同手机设备',
}

# crowd_limit_type 映射
_CROWD_LIMIT_MAP = {
    'notLimit': '无限制',
    'CROWDRULEID': '按人群规则限制',
    'COMMONCONFIG': '限制用户实名',
}

# 风控方案映射
_CTU_VALIDATE_MAP = {
    'REAL_TIME': '咨询实时风控',
    'OFFLINE': '咨询离线风控',
}

# config_checks 字段名映射
_RULE_NAME_MAP = {
    'frequency_control': '频次控制合理性校验',
    'risk_control': '风控咨询校验',
    'realname_limit': '实名/人群限制校验',
    'valid_pl': 'PL有效期校验',
}

# 出资方映射
_PROVIDER_TYPE_MAP = {
    'FUND_TYPE_ANT': '蚂蚁集团',
    'FUND_TYPE_MERCHANT': '商户',
    'FUND_TYPE_THIRD': '第三方',
}

# 活动来源映射
_SUB_BIZ_TYPE_MAP = {
    'lotteryCamp': '自动抽奖',
    'directCamp': '直发',
    'taskCamp': '任务',
}


def _format_timestamp(ms) -> str:
    """毫秒时间戳转 YYYY-MM-DD HH:mm:ss"""
    if not ms:
        return ''
    try:
        from datetime import datetime
        ts = int(ms)
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(ms)


def _format_dims(dimensions) -> str:
    """将维度列表转为中文描述"""
    if not dimensions or not isinstance(dimensions, list):
        return ''
    names = [_DIM_MAP.get(d, d) for d in dimensions if d]
    return '、'.join(names) if names else ''


def _format_freq_limit(model, is_frequency=False) -> str:
    """将频次限制模型转为中文描述"""
    if not model or not isinstance(model, dict):
        return '无限制'

    limit_control = model.get('limitControl', 'notLimit')
    if limit_control != 'limit':
        return '无限制'

    if not is_frequency:
        count = model.get('limitCount', 0)
        dims = model.get('limitDimension', [])
        dim_text = _format_dims(dims)
        if dim_text:
            return f'{dim_text}终身限{count}次'
        return f'终身限{count}次'

    freq_control = model.get('userFrequencyLimitControl', 'notLimit')
    if freq_control != 'limit':
        return '无限制'

    freq_limit = model.get('userFrequencyLimit', 0)
    dims = model.get('countFrequencyDimension', []) or model.get('limitDimension', [])
    dim_text = _format_dims(dims)
    freq_type = model.get('userFrequencyLimitType', 'D')
    period_map = {'D': '每天', 'W': '每周', 'M': '每月', 'Y': '每年'}
    period = period_map.get(freq_type, '每天')

    if dim_text:
        return f'{dim_text}{period}限{freq_limit}次'
    return f'{period}限{freq_limit}次'


def _get_risk_control_desc(data) -> str:
    """根据 ctu_validate_level + skip_ctu_validate 判断活动风控方案"""
    ctu = data.get('ctu_validate_level', 'NONE') or 'NONE'
    skip = data.get('skip_ctu_validate', '')

    if ctu in ('REAL_TIME',):
        return '咨询实时风控'
    if ctu in ('OFFLINE',):
        return '咨询离线风控'
    if str(skip).lower() == 'true':
        return '不咨询风控'
    return '咨询离线风控'


def _get_prize_freq_text(prize_id, data) -> str:
    """获取单个奖品的频次限制描述"""
    # 从 data['prizes'] 中查找（preprocess_row 已提取）
    prizes_data = data.get('prizes', [])
    for prize in prizes_data:
        p_prize_id = prize.get('prize_id', '')
        if str(p_prize_id) != str(prize_id):
            continue

        count_model = prize.get('controls', {}).get('count_model')
        if count_model and isinstance(count_model, dict) and count_model.get('limitControl') == 'limit':
            count = count_model.get('limitCount', 0)
            dims = count_model.get('limitDimension', [])
            dim_text = _format_dims(dims)
            if dim_text:
                return f'{dim_text}终身限{count}次'
            return f'终身限{count}次'

        freq_model = prize.get('controls', {}).get('freq_model') or prize.get('controls', {}).get('count_frequency_model')
        if freq_model and isinstance(freq_model, dict) and freq_model.get('limitControl') == 'limit':
            return _format_freq_limit(freq_model, is_frequency=True)

    # 回退：从 _raw_data 查找
    raw = data.get('_raw_data', {})
    prize_list = raw.get('prizeBasicInfoAll', [])
    if isinstance(prize_list, str):
        try:
            import json as _json
            prize_list = _json.loads(prize_list)
        except Exception:
            prize_list = []

    for prize in prize_list:
        p_prize_id = prize.get('prizeId') or prize.get('id', '')
        if str(p_prize_id) != str(prize_id):
            continue

        count_model = prize.get('prizeCountLimitModel') or prize.get('countLimitModel')
        if count_model and isinstance(count_model, dict) and count_model.get('limitControl') == 'limit':
            count = count_model.get('limitCount', 0)
            dims = count_model.get('limitDimension', [])
            dim_text = _format_dims(dims)
            if dim_text:
                return f'{dim_text}终身限{count}次'
            return f'终身限{count}次'

        freq_model = prize.get('prizeCountFrequencyModel') or prize.get('countFrequencyModel')
        if freq_model and isinstance(freq_model, dict) and freq_model.get('limitControl') == 'limit':
            return _format_freq_limit(freq_model, is_frequency=True)

    return '无限制'


def _map_value_level(level: str) -> str:
    """价值等级映射"""
    level_map = {'高': '高价值', '中': '中价值', '低': '低价值', '未识别': '未识别'}
    return level_map.get(level, level)


def to_final_res(data) -> dict:
    """
    将 pipeline 输出转为线上 final_res 格式。

    纯映射函数：所有数据从 data dict 中直接取值，不再解析 _raw_data。
    数据提取统一在 preprocess_row() 中完成。

    Args:
        data: run_pipeline() 的返回值（preprocess_row 提取的字段 + enrich 链追加的字段）

    Returns:
        dict: 线上 final_res 格式
    """

    # =========================================================
    # 一、基础要素
    # =========================================================
    camp_name = data.get('camp_name', '')
    camp_code = data.get('camp_code', '')
    plan_name_val = data.get('plan_name', '')
    plan_id = data.get('plan_id', '')
    plan_background = data.get('plan_background', '')
    display_rules = data.get('display_rules', '')
    bu_name = data.get('bu_name', '')
    provider_type_raw = data.get('provider_type', '')
    provider_type = _PROVIDER_TYPE_MAP.get(provider_type_raw, provider_type_raw)
    budget_amount = data.get('budget_amount', '')
    camp_gmt_begin = _format_timestamp(data.get('camp_gmt_begin'))
    camp_gmt_end = _format_timestamp(data.get('camp_gmt_end'))
    plan_gmt_begin = _format_timestamp(data.get('plan_gmt_begin'))
    plan_gmt_end = _format_timestamp(data.get('pl_gmt_end'))
    biz_owner = data.get('biz_owner', '')
    creator = data.get('creator', '')
    sub_biz_type_raw = data.get('sub_biz_type', '')
    sub_biz_type = _SUB_BIZ_TYPE_MAP.get(sub_biz_type_raw, sub_biz_type_raw)
    config_code = data.get('config_code', '')

    # =========================================================
    # 二、活动限制
    # =========================================================

    # 1. 活动类型（dapro + environment）
    is_dapro = data.get('is_dapro', False)
    environment = data.get('environment', '') or ''
    if environment in ('PRE_ENV', 'PRERELEASE_ENV'):
        activity_type = '预发-大促' if is_dapro else '预发-日常活动'
    else:
        activity_type = '大促' if is_dapro else '日常活动'

    # 2. 人群限制（获取限制）
    crowd_limit_type = data.get('crowd_limit_type', '')
    access_limit = _CROWD_LIMIT_MAP.get(crowd_limit_type, crowd_limit_type or '未配置')

    # 3. 是否限制实名
    realname_auth = data.get('realname_auth', 0)
    is_realname = '是' if realname_auth and int(realname_auth) != 0 else '否'

    # 4. 是否限制四同 → 字符串格式（通过/不通过 + reason）
    config_checks = data.get('config_checks', {})
    freq_check = config_checks.get('frequency_control', {})
    if isinstance(freq_check, dict):
        freq_pass = freq_check.get('pass', True)
        freq_reason = freq_check.get('reason', '未检查')
        frequency_control = ('通过' if freq_pass else '不通过') + ('。' + freq_reason if freq_reason else '')
    else:
        frequency_control = '通过。未检查'

    # 四同字段详细校验结果（用于"详细校验结果"输出，保持原逻辑不变）
    frequency_control_detail = {
        'pass': freq_check.get('pass', True) if isinstance(freq_check, dict) else True,
        'reason': freq_check.get('reason', '未检查') if isinstance(freq_check, dict) else '未检查',
    }

    # 5. 是否地域限制
    lbs_limit = data.get('lbs_limit', '')
    is_lbs_limit = '是' if lbs_limit and lbs_limit != 'NO_LIMIT' else '未知'

    # 6-9. 频次限制4个字段
    freq_6 = _format_freq_limit(data.get('freq_count_limit'), is_frequency=False)
    freq_7 = _format_freq_limit(data.get('freq_count_frequency'), is_frequency=True)
    freq_8 = _format_freq_limit(data.get('freq_prize_count_limit'), is_frequency=False)
    freq_9 = _format_freq_limit(data.get('freq_prize_count_frequency'), is_frequency=True)

    # 是否所有限制四同
    prizes_data = data.get('prizes', [])
    all_prize_4same = '是' if prizes_data and all(p.get('controls', {}).get('dim_count', 0) >= 4 for p in prizes_data) else '否'

    # 证件号为空是否默认通过
    id_card_empty = data.get('id_card_empty', 'false')
    id_card_pass = '默认通过' if str(id_card_empty).lower() == 'true' else '默认不通过'

    # 活动风控方案
    risk_control_desc = _get_risk_control_desc(data)

    # =========================================================
    # 三、奖品配置
    # =========================================================
    prize_values = data.get('prize_values', {})

    # prize_values: 转为list格式，每个元素为 "PZ号,prize_name"
    prize_values_output = []
    for prize_id, pv in prize_values.items():
        prize_name = pv.get('prize_name', '')
        prize_values_output.append(f'{prize_id},{prize_name}')

    # 奖品散点数据
    prize_scatter = []
    for i, (prize_id, pv) in enumerate(prize_values.items(), 1):
        scatter_item = {
            "id": i,
            "name": pv.get('prize_name', ''),
            "maxValue": pv.get('true_value', 0),
            "minValue": pv.get('min_value', pv.get('true_value', 0)),
            "budgetType": pv.get('benefit_type', ''),
            "valueLevel": _map_value_level(pv.get('value_level', '')),
            "frequencyLimit": _get_prize_freq_text(prize_id, data),
        }
        prize_scatter.append(scatter_item)

    # =========================================================
    # 四、评审结论
    # =========================================================

    # 1. 单用户单日激励金额期望值
    activity_expected_value = data.get('activity_expected_value', 0)
    daily_ev_text = f'{activity_expected_value}元' if activity_expected_value else '0元'

    # 2. 活动配置是否合理
    has_config_risk = data.get('has_config_risk', False)
    config_risk_text = '有配置风险' if has_config_risk else '无配置风险'

    # 3. 活动配置是否合理原因
    config_risk_reasons = data.get('config_risk_reasons', [])
    config_risk_reason_text = '；'.join(config_risk_reasons) if config_risk_reasons else '配置校验通过'

    # 4. 详细校验结果
    detail_check_list = []
    for check_key, check_val in config_checks.items():
        if isinstance(check_val, dict):
            detail_check_list.append({
                'rule_name': _RULE_NAME_MAP.get(check_key, check_key),
                'status': '通过' if check_val.get('pass') else '不通过',
                'reason': check_val.get('reason', ''),
            })

    # 5. 是否有风险
    has_risk = data.get('has_risk', False)
    has_risk_text = '是' if has_risk else '否'

    # 6. 风险判断原因（详细版）
    reasons = []
    for check_key, check_val in config_checks.items():
        if isinstance(check_val, dict) and not check_val.get('pass'):
            rule_name = _RULE_NAME_MAP.get(check_key, check_key)
            reasons.append(f'【配置风险】{rule_name}: {check_val.get("reason", "")}')
            for detail in check_val.get('details', []):
                if isinstance(detail, dict) and not detail.get('pass'):
                    reasons.append(f'  - 奖品{detail.get("prize_name", "")}: {detail.get("reason", "")}')
    biz_checks = data.get('biz_checks', {})
    if isinstance(biz_checks, dict):
        for check_key, check_val in biz_checks.items():
            if isinstance(check_val, dict) and not check_val.get('pass'):
                reasons.append(f'【业务风险】{check_val.get("reason", "")}')
            elif isinstance(check_val, list):
                for item in check_val:
                    if isinstance(item, dict) and not item.get('pass'):
                        reasons.append(f'【业务风险】{item.get("reason", "")}')
    risk_reason_text = '；'.join(reasons) if reasons else '无风险'

    # =========================================================
    # 组装 final_res
    # =========================================================
    final_res = {}

    # 一、基础要素
    final_res['活动名称'] = camp_name
    final_res['活动CP号'] = camp_code
    final_res['方案名称'] = plan_name_val
    final_res['方案PL号'] = plan_id
    final_res['方案背景'] = plan_background
    final_res['对用户展示规则'] = display_rules
    final_res['业务线'] = bu_name
    final_res['出资方'] = provider_type
    final_res['方案预算'] = budget_amount
    final_res['活动开始时间'] = camp_gmt_begin
    final_res['活动结束时间'] = camp_gmt_end
    final_res['方案开始时间'] = plan_gmt_begin
    final_res['方案结束时间'] = plan_gmt_end
    final_res['活动需求方'] = biz_owner
    final_res['活动创建人'] = creator
    final_res['活动来源'] = config_code or sub_biz_type
    final_res['活动类型'] = activity_type

    # 二、活动限制
    final_res['获取限制'] = access_limit
    final_res['是否限制实名'] = is_realname
    final_res['是否限制四同'] = frequency_control
    final_res['是否地域限制'] = is_lbs_limit
    final_res['参与活动次数限制'] = freq_6
    final_res['参与活动频次限制'] = freq_7
    final_res['活动中奖次数限制'] = freq_8
    final_res['活动中奖频次限制'] = freq_9
    final_res['证件号为空是否默认通过'] = id_card_pass
    final_res['活动风控方案'] = risk_control_desc
    final_res['是否所有奖品都限制了四同'] = all_prize_4same

    # 三、奖品配置
    final_res['奖品配置信息'] = prize_values_output
    final_res['奖品散点数据'] = prize_scatter

    # 四、评审结论
    final_res['单用户单日激励金额期望值'] = daily_ev_text
    final_res['活动配置是否合理'] = config_risk_text
    final_res['活动配置是否合理原因'] = config_risk_reason_text
    final_res['详细校验结果'] = detail_check_list
    final_res['是否有风险'] = has_risk_text
    final_res['风险判断原因'] = risk_reason_text

    # 防控模块（暂时不配置）
    final_res['防控模块推荐'] = []
    final_res['防控模块推荐原因'] = ''
    final_res['感知模块推荐'] = []

    return final_res


def run_from_file(event_property_path: str) -> dict:
    """
    从 event_property JSON 文件运行 pipeline 并转为 final_res。

    Args:
        event_property_path: event_property_{id}.json 文件路径

    Returns:
        dict: final_res 格式
    """
    import json as _json

    with open(event_property_path, 'r', encoding='utf-8') as f:
        ep_data = _json.load(f)

    # 兼容 MCP 格式：包装为 examinationBasicInfo
    if 'examinationBasicInfo' not in ep_data and 'campBasicInfo_new' in ep_data:
        ep_wrapped = {'examinationBasicInfo': ep_data}
        ep_str = _json.dumps(ep_wrapped, ensure_ascii=False)
    elif 'examinationBasicInfo' in ep_data:
        ep_str = _json.dumps(ep_data, ensure_ascii=False)
    else:
        ep_str = _json.dumps(ep_data, ensure_ascii=False)

    row = {'event_property': ep_str}
    result = run_pipeline(row)
    return to_final_res(result)
