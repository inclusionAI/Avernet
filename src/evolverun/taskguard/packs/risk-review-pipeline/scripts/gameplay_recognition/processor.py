# -*- coding: utf-8 -*-
"""
活动玩法识别模块 (Pipeline版)

从活动名称(campName)、方案名称(planName)、奖品名称(prizeName)、
活动描述(participationContent)及结构化字段(subBizType/camptype)中识别活动玩法。
"""

import re
from typing import Dict, List, Tuple

# ============================================================================
# 玩法定义及关键词
# ============================================================================

GAMEPLAY_RULES = [
    # === 金融玩法（优先匹配，更具体） ===
    {
        'name': '贷后还款类',
        'keywords': ['逾期', '帮扶金', '贷后'],
        'desc': '已逾期用户的帮扶政策，减免部分利息/滞纳金/本金',
    },
    {
        'name': '企业营销',
        'keywords': ['企业', 'B端', '西进户', '白鹭户', '电商账户'],
        'desc': '企业用户完成任务后获得较高权益',
    },
    {
        'name': '申购类-体验金',
        'keywords': ['体验金'],
        'exclude_keywords': ['ETF体验金'],
        'desc': '虚拟理财申购本金，投资后获得收益',
    },
    {
        'name': '申购类-申购立减红包',
        'keywords': ['申购红包', '申购立减', '财运红包', '理财红包', '转入红包', '买入红包'],
        'desc': '申购理财产品时减免申购成本',
    },
    {
        'name': '申购类-理财大促',
        'keywords': ['理财大促'],
        'extra_check': 'prize_count_gt_20',
        'desc': '理财申购的复杂升级玩法',
    },
    {
        'name': '申购类-申购任务',
        'keywords': ['申购任务', '申购后', '买入后', '转入后'],
        'desc': '完成基金申购后发放权益',
    },
    {
        'name': '申购类-会员等级',
        'keywords': ['会员等级', '黑卡等级', '会员权益'],
        'desc': '申购金额和留存达标后获得会员等级',
    },
    {
        'name': '信贷支用类-减收',
        'keywords': ['息费减免', '免息', '利率折扣', '利率打折', '减收'],
        'desc': '信贷支用过程中获得息费减免',
    },
    {
        'name': '信贷支用类-任务',
        'keywords': ['支用任务', '支用后', '支用发'],
        'desc': '完成信贷支用任务后获得权益',
    },
    {
        'name': '授信类',
        'keywords': ['授信', '提额', '查询额度', '首次授信'],
        'desc': '完成授信行为后获得权益',
    },
    {
        'name': '开通类',
        'keywords': ['开通', '开户', '开卡'],
        'desc': '开通某种功能或服务后获得权益',
    },
    {
        'name': '自证类',
        'keywords': ['自证', '身份证明'],
        'desc': '提交凭证校验后获得权益',
    },
    {
        'name': '支付类-分期交易',
        'keywords': ['分期', '分期支付', '分期交易', '分期立减'],
        'desc': '花呗/信用卡分期支付场景',
    },
    # === 基础玩法 ===
    {
        'name': '秒杀类',
        'keywords': ['秒杀', '限时抢购', '先到先得', '限时抢'],
        'desc': '固定时刻或范围内先到先得',
    },
    {
        'name': '红包雨',
        'keywords': ['红包雨'],
        'desc': '页面红包飘出，用户点击获取',
    },
    {
        'name': '签到打卡类',
        'keywords': ['签到', '打卡'],
        'struct_field': {'camptype_contains': 'SIGNIN'},
        'desc': '完成签到类任务发奖',
    },
    {
        'name': '线上人传人',
        'keywords': ['人传人', '邀请', '分享', '裂变', '老带新', '被邀请'],
        'struct_field': {'camptype_contains': 'COMMON_SNS_APP'},
        'desc': '线上邀请关系的分享类活动',
    },
    {
        'name': '线下人传人',
        'keywords': ['线下人传人', '口播', '商户赏金', '收银员'],
        'desc': '线下当面邀请完成任务',
    },
    {
        'name': '游戏类',
        'keywords': ['游戏', '通关', '闯关', '得分'],
        'desc': '游戏环节中或通关后发奖',
    },
    {
        'name': '排名打榜类',
        'keywords': ['排名', '打榜', '榜单', '排行榜', '竞争'],
        'desc': '用户或群组竞争榜单',
    },
    {
        'name': '浏览类',
        'keywords': ['浏览', '观看', '视频'],
        'desc': '浏览指定页面后发奖',
    },
    {
        'name': '线下任务类',
        'keywords': ['线下任务', '铺设机具', '上门拍照', '门店踩点'],
        'desc': '完成线下作业任务后发奖',
    },
    {
        'name': '线上任务类',
        'keywords': ['任务'],
        'struct_field': {'camptype_contains': 'COMMON_TASK_PLAN_APP'},
        'desc': '完成线上组合任务后发奖',
    },
    {
        'name': '立减类',
        'keywords': ['立减', '立享'],
        'desc': '当笔支付直接优惠',
    },
    {
        'name': '支付类',
        'keywords': ['支付红包', '支付后', '交易后', '消费后'],
        'desc': '完成支付后发放红包',
    },
    {
        'name': '抽奖类',
        'keywords': ['抽奖', '大转盘', '转盘'],
        'struct_field': {'subbiztype': 'lotteryCamp'},
        'desc': '系统抽奖，金额不固定',
    },
    {
        'name': '物料领取类',
        'keywords': ['物料', '机具', '门票'],
        'desc': '领取实物物料后发奖',
    },
]

DAPRO_KEYWORDS = ['大促', '618', '双11', '双十一', '周年', '狂欢节', '购物节', '年货节',
                   '开门红', '盛典', '庆典', '春运', '开业', '店庆', '节']


# ============================================================================
# 识别函数
# ============================================================================

def _match_keywords(text, keywords):
    """在文本中匹配关键词，返回匹配到的第一个关键词"""
    if not text:
        return ''
    for kw in keywords:
        if kw in text:
            return kw
    return ''


def _match_struct(rule, sub_biz_type, camp_type):
    """匹配结构化字段"""
    sf = rule.get('struct_field')
    if not sf:
        return False
    if 'subbiztype' in sf and sub_biz_type == sf['subbiztype']:
        return True
    if 'camptype_contains' in sf and sf['camptype_contains'] in camp_type:
        return True
    return False


def identify_gameplay(data):
    """
    识别活动玩法 (从 pipeline data dict 读取)

    Args:
         pipeline 标准化 dict 或原始 JSON dict

    Returns:
        [{name, matched_keyword, matched_source, desc}]
    """
    # 支持两种输入：pipeline data dict 或 原始 JSON
    if 'camp_name' in data:
        # Pipeline data dict
        camp_name = data.get('camp_name', '') or ''
        plan_name = data.get('plan_name', '') or ''
        prize_names = [p.get('prize_name', '') for p in data.get('prizes', [])]
        prize_count = data.get('prize_count', 0)
        # 从 _raw_data 获取结构化字段和活动描述
        raw = data.get('_raw_data') or {}
        try:
            exam = raw.get('examinationBasicInfo', {}) or {}
            cbin = exam.get('campBasicInfoNew', {}) or {}
            dc_raw = cbin.get('detailContent', {}) or {}
            sub_biz_type = cbin.get('subBizType', '') or ''
            camp_type = cbin.get('configCode', '') or ''
            if not camp_type:
                camp_type = dc_raw.get('templateConfigName', '') or ''
            participation = dc_raw.get('participationContent', '') or ''
        except (KeyError, TypeError, AttributeError):
            sub_biz_type = ''
            camp_type = ''
            participation = ''
    else:
        # Legacy: raw JSON dict
        try:
            exam = data.get('examinationBasicInfo', {})
            camp_name = exam.get('campName', '') or ''
            plan_name = ''
            try:
                plan_name = exam.get('planBasicInfo', {}).get('planWorkOrderDTO', {}).get('planName', '') or ''
            except (AttributeError, TypeError):
                pass
            prize_names = []
            for pi in exam.get('prizeBasicInfoAll', []):
                pn = pi.get('prizeBaseInfoDTO', {}).get('prizeName', '')
                if pn:
                    prize_names.append(pn)
            prize_count = len(exam.get('prizeBasicInfoAll', []))
            cbin = exam.get('campBasicInfoNew', {})
            dc_raw = cbin.get('detailContent', {})
            sub_biz_type = cbin.get('subBizType', '') or ''
            camp_type = cbin.get('configCode', '') or ''
            if not camp_type:
                camp_type = dc_raw.get('templateConfigName', '') or ''
            participation = dc_raw.get('participationContent', '') or ''
        except (KeyError, TypeError, AttributeError):
            return []

    all_prize_names = ' '.join(prize_names)
    all_text = '{} {} {} {}'.format(camp_name, plan_name, all_prize_names, participation)

    results = []
    matched_names = set()

    for rule in GAMEPLAY_RULES:
        name = rule['name']
        if name in matched_names:
            continue

        keywords = rule.get('keywords', [])
        exclude = rule.get('exclude_keywords', [])

        excluded = False
        for ek in exclude:
            if ek in all_text:
                excluded = True
                break
        if excluded:
            continue

        matched_kw = ''
        matched_src = ''

        kw = _match_keywords(camp_name, keywords)
        if kw:
            matched_kw = kw
            matched_src = 'campName'
        if not matched_kw:
            kw = _match_keywords(plan_name, keywords)
            if kw:
                matched_kw = kw
                matched_src = 'planName'
        if not matched_kw:
            kw = _match_keywords(all_prize_names, keywords)
            if kw:
                matched_kw = kw
                matched_src = 'prizeName'
        if not matched_kw:
            kw = _match_keywords(participation, keywords)
            if kw:
                matched_kw = kw
                matched_src = 'participationContent'

        struct_match = _match_struct(rule, sub_biz_type, camp_type)

        extra = rule.get('extra_check', '')
        if extra == 'prize_count_gt_20' and prize_count > 20:
            if not matched_kw:
                matched_kw = 'PZ数={}'.format(prize_count)
                matched_src = 'prize_count'

        if matched_kw or struct_match:
            result = {
                'name': name,
                'matched_keyword': matched_kw if matched_kw else '结构化字段',
                'matched_source': matched_src if matched_src else 'struct',
                'desc': rule.get('desc', ''),
            }
            results.append(result)
            matched_names.add(name)

    return results


def identify_gameplay_names(data):
    """简化版：只返回玩法名称列表"""
    return [r['name'] for r in identify_gameplay(data)]


def is_dapro(data):
    """
    大促识别

    Returns:
        (是否大促, 匹配说明)
    """
    if 'camp_name' in data:
        camp_name = data.get('camp_name', '') or ''
        plan_name = data.get('plan_name', '') or ''
        prize_count = data.get('prize_count', 0)
    else:
        try:
            exam = data.get('examinationBasicInfo', {})
            camp_name = exam.get('campName', '') or ''
            plan_name = ''
            try:
                plan_name = exam.get('planBasicInfo', {}).get('planWorkOrderDTO', {}).get('planName', '') or ''
            except Exception:
                pass
            prize_count = len(exam.get('prizeBasicInfoAll', []))
        except Exception:
            return False, ''

    for kw in DAPRO_KEYWORDS:
        if kw in camp_name:
            return True, 'campName含"{}"'.format(kw)
        if kw in plan_name:
            return True, 'planName含"{}"'.format(kw)

    if prize_count > 20:
        return True, 'PZ数={}>20'.format(prize_count)

    return False, ''


# ============================================================================
# Pipeline enrich 接口
# ============================================================================

def enrich(data):
    """
    Pipeline enrichment: 识别活动玩法和大促。

    添加字段:
    - gameplay_names: list[str]
    - gameplays: list[dict]
    - is_dapro: bool
    - dapro_reason: str
    """
    if data is None or data.get('_parse_failed'):
        data['gameplay_names'] = []
        data['gameplays'] = []
        data['is_dapro'] = False
        data['dapro_reason'] = ''
        return data

    gameplays = identify_gameplay(data)
    dapro_flag, dapro_reason = is_dapro(data)

    data['gameplay_names'] = [g['name'] for g in gameplays]
    data['gameplays'] = gameplays
    data['is_dapro'] = dapro_flag
    data['dapro_reason'] = dapro_reason
    return data
