"""
权益价值识别模块

从奖品名称和价格配置中提取奖品的真实价值。

产品分两大类：
1. 非利息类产品 - 有固定换算系数
   - 底表存分的(maxPrice需×0.01再×系数): 黄金票/保障金/提前收款卡/促开卡红包/财运红包/余额宝红包
   - 底表存个数的(maxPrice直接×系数): 花呗金/安心豆/福利金/数币元宝
2. 利息类产品 - 底表存分, maxPrice需×0.01
   - 天数相关: 余额宝/余利宝/增利宝/股票/其他体验金 → 系数=day_base×天数
   - 固定系数: 免息券/分期收银台打折权益/分期收银台免息权益/利率打折卡

计算逻辑：
- maxPrice处理: 存分→maxPrice×0.01×系数; 存个数→maxPrice×系数
- 奖品名解析: 解析为元+非利息→直接取; 解析为个数+非利息→乘系数; 解析为元+利息→乘系数

注意：奖品产品类型统一由 classify_prize_benefit 识别（基于结构化字段），
不再通过名称关键词判断产品类型。名称只用于提取金额数值和天数。
"""

import re
from typing import Tuple, Optional

# ============================================================================
# 产品换算配置
# ============================================================================

# 非利息类产品 - 底表存分，maxPrice需要 × 0.01
# min_value: 计算结果和此值取大（如实物奖品至少10元）
NON_INTEREST_FEN = {
    # === 原有配置 ===
    '黄金票':     {'coefficient': 0.001, 'desc': '份数×0.001'},
    '保障金':     {'coefficient': 0.01,  'desc': '面额×0.01'},
    '提前收款卡': {'coefficient': 0.01,  'desc': '面额×0.01'},
    '促开卡红包': {'coefficient': 1,     'desc': '面额即价值'},
    '财运红包':   {'coefficient': 1,     'desc': '面额即价值'},
    '余额宝红包': {'coefficient': 1,     'desc': '面额即价值'},
    # === 系数=1（面额即价值）===
    '场景红包':     {'coefficient': 1, 'desc': '面额即价值'},
    '现金红包':     {'coefficient': 1, 'desc': '面额即价值'},
    '理财红包':     {'coefficient': 1, 'desc': '面额即价值'},
    '还款红包':     {'coefficient': 1, 'desc': '面额即价值'},
    '网商账户红包': {'coefficient': 1, 'desc': '面额即价值'},
    '支付红包':     {'coefficient': 1, 'desc': '面额即价值'},
    '花借还款红包': {'coefficient': 1, 'desc': '面额即价值'},
    '代金券':       {'coefficient': 1, 'desc': '面额即价值'},
    '渠道券':       {'coefficient': 1, 'desc': '面额即价值'},
    '网商积分奖品': {'coefficient': 1, 'desc': '面额即价值(暂按1)'},
    '折扣券':       {'coefficient': 1, 'desc': '面额即价值(暂按1)'},
    '积分':         {'coefficient': 1, 'desc': '面额即价值(暂按1)'},
    # === 系数=0（无实际货币价值）===
    '虚拟奖品': {'coefficient': 0, 'desc': '无实际货币价值'},
    '福卡':     {'coefficient': 0, 'desc': '无实际货币价值'},
    '额度券':   {'coefficient': 0, 'desc': '无实际货币价值(借钱要还)'},
    '数字藏品': {'coefficient': 0, 'desc': '无实际货币价值'},
    '凭证':     {'coefficient': 0, 'desc': '无实际货币价值'},
    # === 计算后和10元取大 ===
    '实物奖品':     {'coefficient': 1, 'desc': '面额即价值,至少10元', 'min_value': 10},
    '兑换券':       {'coefficient': 1, 'desc': '面额即价值,至少10元', 'min_value': 10},
    '网商会员权益': {'coefficient': 1, 'desc': '面额即价值,至少10元', 'min_value': 10},
    '组合奖品':     {'coefficient': 1, 'desc': '面额即价值,至少10元', 'min_value': 10},
    # === 特殊系数 ===
    '汇率优惠券':   {'coefficient': 0.1,    'desc': '面额×0.1'},
    '会员权益':     {'coefficient': 1, 'desc': '面额即价值,至少10元', 'min_value': 10},
}

# 非利息类产品 - 底表存个数，maxPrice不需要 × 0.01
# 名称中写"xx元"时直接取值，写"xx个"时需乘系数
NON_INTEREST_COUNT = {
    '花呗金':       {'coefficient': 0.01,   'desc': '个数×0.01'},
    '安心豆':       {'coefficient': 0.01,   'desc': '个数×0.01'},
    '福利金':       {'coefficient': 0.0001, 'desc': '个数×0.0001'},
    '数币元宝':     {'coefficient': 0.0001, 'desc': '个数×0.0001'},
    '绿色经营金币': {'coefficient': 0.0001, 'desc': '同福利金,个数×0.0001'},
}

# 利息类产品 - 底表存分，系数与天数相关
# 天数逻辑：从奖品名提取"n天"，未找到默认7天
INTEREST_DAY_BASED = {
    '余额宝体验金': {'day_base': 0.02 / 365, 'desc': '0.02/365×天数'},
    '余利宝体验金': {'day_base': 0.02 / 365, 'desc': '0.02/365×天数'},
    '增利宝体验金': {'day_base': 0.1,        'desc': '0.1×天数'},
    '股票体验金':   {'day_base': 0.1,        'desc': '0.1×天数'},
}

# 其他体验金的默认配置（理财/周利宝/稳利宝/月利宝等）
DEFAULT_EXPERIENCE_CONFIG = {'day_base': 0.001, 'desc': '0.001×天数'}

# 利息类产品 - 底表存分，固定系数（不与天数相关）
INTEREST_FIXED = {
    '免息券':           {'coefficient': 0.033,  'desc': '面额×0.033'},
    '分期收银台打折权益': {'coefficient': 0.003,  'desc': '面额×0.003'},
    '分期收银台免息权益': {'coefficient': 0.0005, 'desc': '面额×0.0005'},
    '利率打折卡':       {'coefficient': 0.0005, 'desc': '面额×0.0005'},
}

DEFAULT_DAYS = 7


def _get_product_config(benefit_type: str) -> Optional[dict]:
    """
    根据 benefit_type 获取产品配置

    Returns:
        dict with keys: category('non_interest_fen'/'non_interest_count'/'interest_day'/'interest_fixed'),
                        coefficient or day_base, desc
        None if not a special product
    """
    if benefit_type in NON_INTEREST_FEN:
        cfg = NON_INTEREST_FEN[benefit_type]
        return {**cfg, 'category': 'non_interest_fen'}
    if benefit_type in NON_INTEREST_COUNT:
        cfg = NON_INTEREST_COUNT[benefit_type]
        return {**cfg, 'category': 'non_interest_count'}
    if benefit_type in INTEREST_DAY_BASED:
        cfg = INTEREST_DAY_BASED[benefit_type]
        return {**cfg, 'category': 'interest_day'}
    if benefit_type in INTEREST_FIXED:
        cfg = INTEREST_FIXED[benefit_type]
        return {**cfg, 'category': 'interest_fixed'}
    # 未显式列出的体验金类型 → 使用默认体验金配置
    if '体验金' in benefit_type:
        return {**DEFAULT_EXPERIENCE_CONFIG, 'category': 'interest_day'}
    return None


def _is_interest_product(config: dict) -> bool:
    """判断是否为利息类产品"""
    return config['category'] in ('interest_day', 'interest_fixed')


def _get_coefficient(config: dict, days: int = DEFAULT_DAYS) -> float:
    """从配置中计算实际系数"""
    if 'coefficient' in config:
        return config['coefficient']
    if 'day_base' in config:
        return config['day_base'] * days
    return 1


def extract_days_from_name(prize_name: str) -> int:
    """从奖品名中提取天数，未找到则默认7天"""
    if not prize_name:
        return DEFAULT_DAYS
    match = re.search(r'(\d+)\s*天', prize_name)
    return int(match.group(1)) if match else DEFAULT_DAYS


def extract_value_from_name(prize_name: str, prefer_count_unit: bool = False) -> Tuple[float, bool, str, str]:
    """
    从奖品名中正则提取金额和单位

    匹配模式:
    - 模式A: "X万" / "Xw" / "X万元" → X × 10000，单位=元
    - 模式B: "X元" → X，单位=元
    - 模式C: "X个" / "X份" / "X张" → X，单位=个数

    当 prefer_count_unit=True 时（已知是存个数类产品），优先匹配模式C，
    找不到才 fallback 到模式A/B。避免"黄金票88份"误取条件中的"1200元"。

    Returns:
        (提取的数值, 是否成功提取, 提取说明, 单位类型: 'yuan'/'count'/'')
    """
    if not prize_name:
        return 0, False, '', ''

    if prefer_count_unit:
        # 优先匹配个数单位
        match = re.search(r'(\d+(?:\.\d+)?)\s*(?:个|份|张)', prize_name)
        if match:
            amount = float(match.group(1))
            return amount, True, f'从名称提取: {match.group(0)} = {amount}(优先个数)', 'count'
        # fallback 到元单位
        match = re.search(r'(\d+(?:\.\d+)?)\s*(?:万|w)\s*(?:元)?', prize_name, re.IGNORECASE)
        if match:
            amount = float(match.group(1)) * 10000
            return amount, True, f'从名称提取: {match.group(0).strip()} = {amount}', 'yuan'
        match = re.search(r'(\d+(?:\.\d+)?)\s*元', prize_name)
        if match:
            amount = float(match.group(1))
            return amount, True, f'从名称提取: {match.group(0)} = {amount}', 'yuan'
        return 0, False, '', ''

    # 模式A: X万 / Xw / X万元（单位=元）
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:万|w)\s*(?:元)?', prize_name, re.IGNORECASE)
    if match:
        amount = float(match.group(1)) * 10000
        return amount, True, f'从名称提取: {match.group(0).strip()} = {amount}', 'yuan'

    # 模式B: X元（单位=元）
    match = re.search(r'(\d+(?:\.\d+)?)\s*元', prize_name)
    if match:
        amount = float(match.group(1))
        return amount, True, f'从名称提取: {match.group(0)} = {amount}', 'yuan'

    # 模式C: X个 / X份 / X张（单位=个数）
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:个|份|张)', prize_name)
    if match:
        amount = float(match.group(1))
        return amount, True, f'从名称提取: {match.group(0)} = {amount}', 'count'

    return 0, False, '', ''


def convert_special_prize_value(benefit_type: str, max_price_raw: float,
                                min_price_raw: float = 0,
                                prize_name: str = '') -> Tuple[float, float, bool, str]:
    """
    对特殊权益类型进行价值换算（基于原始maxPrice）

    Args:
        benefit_type: classify_prize_benefit 返回的权益类型
        max_price_raw: 数据库原始 maxPrice 值（分或个数，未经 /100 处理）
        min_price_raw: 数据库原始 minPrice 值
        prize_name: 奖品名称（用于提取天数）

    Returns:
        (换算后最大价值元, 换算后最小价值元, 是否换算, 换算说明)
    """
    config = _get_product_config(benefit_type)
    if not config:
        return max_price_raw / 100, min_price_raw / 100, False, ''

    days = extract_days_from_name(prize_name)
    coeff = _get_coefficient(config, days)
    category = config['category']

    if category == 'non_interest_count':
        # 存个数: maxPrice直接×系数
        converted_max = max_price_raw * coeff
        converted_min = min_price_raw * coeff
        desc_detail = f"原始值{max_price_raw}×{coeff}"
    else:
        # 存分: maxPrice×0.01×系数
        converted_max = max_price_raw * 0.01 * coeff
        converted_min = min_price_raw * 0.01 * coeff
        if 'day_base' in config:
            desc_detail = f"原始值{max_price_raw}×0.01×{config['day_base']}×{days}天"
        else:
            desc_detail = f"原始值{max_price_raw}×0.01×{coeff}"

    # 如果有 min_value，取大
    min_val = config.get('min_value', 0)
    if min_val and converted_max < min_val:
        desc_detail += f", 取min_value={min_val}"
        converted_max = max(converted_max, min_val)
        converted_min = max(converted_min, min_val)

    desc = f"{benefit_type}({config['desc']}) → {desc_detail}={converted_max:.4f}元"
    return converted_max, converted_min, True, desc


def get_prize_true_value(prize_name: str, max_price_raw: float,
                         benefit_type: str = '') -> Tuple[float, str]:
    """
    按优先级提取奖品真实价值

    优先级1: 从奖品名正则提取金额
      - 非利息类+元: 直接取值（业务已换算好写在名称中）
      - 非利息类+个数: 数值×系数
      - 利息类+元: 数值×系数（名称中的"元"是面额，非真实价值）
    优先级2: 使用 maxPrice（原始值）
      - 存分: maxPrice×0.01×系数
      - 存个数: maxPrice×系数

    Args:
        prize_name: 奖品名称
        max_price_raw: 数据库原始 maxPrice 值（未经 /100）
        benefit_type: classify_prize_benefit 返回的权益类型

    Returns:
        (真实价值元, 来源说明)
    """
    config = _get_product_config(benefit_type)
    days = extract_days_from_name(prize_name)

    def _apply_min_value(value, desc):
        """如果配置有 min_value，取大"""
        if config and config.get('min_value'):
            mv = config['min_value']
            if value < mv:
                return mv, f"{desc}, 取min={mv}元"
        return value, desc

    # 存个数类产品或黄金票/保障金等(系数<1)，名称解析时优先取"个/份/张"
    prefer_count = False
    if config and config['category'] == 'non_interest_count':
        prefer_count = True
    elif config and config['category'] == 'non_interest_fen' and config.get('coefficient', 1) < 1:
        prefer_count = True

    # 优先级1: 从奖品名正则提取金额
    name_value, name_found, name_desc, unit_type = extract_value_from_name(prize_name, prefer_count_unit=prefer_count)
    if name_found and config:
        coeff = _get_coefficient(config, days)
        is_interest = _is_interest_product(config)

        if unit_type == 'yuan' and not is_interest:
            # 非利息类 + 元 → 直接取值
            return _apply_min_value(name_value, f"{name_desc} (非利息类,元=直接取值)")
        elif unit_type == 'count' and not is_interest:
            # 非利息类 + 个数 → ×系数
            true_value = name_value * coeff
            return _apply_min_value(true_value, f"{name_desc}, ×{coeff}({benefit_type}) = {true_value:.4f}元")
        elif is_interest:
            # 利息类 + 任意单位 → ×系数（名称中的"元"是面额）
            true_value = name_value * coeff
            coeff_desc = f"{config.get('day_base', coeff)}×{days}天" if 'day_base' in config else str(coeff)
            return _apply_min_value(true_value, f"{name_desc}, ×{coeff_desc}({benefit_type}) = {true_value:.4f}元")

    if name_found and not config:
        # 非特殊产品，名称解析到值直接使用
        return name_value, name_desc

    # 优先级2: 使用 maxPrice 原始值
    if config:
        coeff = _get_coefficient(config, days)
        category = config['category']
        if category == 'non_interest_count':
            true_value = max_price_raw * coeff
            return _apply_min_value(true_value, f"maxPrice={max_price_raw}×{coeff}({benefit_type}) = {true_value:.4f}元")
        else:
            true_value = max_price_raw * 0.01 * coeff
            if 'day_base' in config:
                return _apply_min_value(true_value, f"maxPrice={max_price_raw}×0.01×{config['day_base']}×{days}天({benefit_type}) = {true_value:.4f}元")
            return _apply_min_value(true_value, f"maxPrice={max_price_raw}×0.01×{coeff}({benefit_type}) = {true_value:.4f}元")

    # 非特殊产品，fallback到 maxPrice/100（分→元）
    max_value = max_price_raw / 100
    if max_value > 0:
        return max_value, f"maxPrice={max_price_raw}/100 = {max_value}元"
    return 0, '无明确价值信息'


def determine_prize_value_level(true_value: float) -> str:
    """
    根据现金价值判定价值档次

    用于后续频次控制风险校验，不同价值档次有不同的频次限制要求：
    - 高价值（≥0.1元）：必须限制四同1次
    - 中价值（0.01~0.1元）：必须限制四同N次，N<100（网商N<10）
    - 低价值（<0.01元）：必须限制一同N次，N<100（网商N<10）

    Args:
        true_value: 奖品真实现金价值（元）

    Returns:
        "高" - 现金价值 >= 0.1元
        "中" - 0.01元 <= 现金价值 < 0.1元
        "低" - 不具备现金价值（< 0.01元）
    """
    if true_value >= 0.1:
        return "高"
    elif true_value >= 0.01:
        return "中"
    else:
        return "低"


# ============================================================================
# 权益类型分类（严格对照语雀文档《非红包权益价值量化词典》SQL）
# ============================================================================

def _name_contains(keyword: str, prize_name: str, camp_name: str,
                   plan_name: str, voucher_template_name: str) -> bool:
    """检查任一名称字段是否包含关键词（对应SQL中的多字段LIKE判断）"""
    for text in (prize_name, camp_name, plan_name, voucher_template_name):
        if text and keyword in text:
            return True
    return False


def _classify_caifu(prize_type: str, prize_sub_type: str,
                    voucher_product_code: str, prize_name: str,
                    camp_name: str, plan_name: str,
                    voucher_template_name: str) -> str:
    """财富场景权益分类 - 严格对照语雀文档SQL"""
    vpc = voucher_product_code or ''
    pst = prize_sub_type or ''
    pt = prize_type or ''

    # WHEN voucher_product_code IN (...) THEN "支付红包"
    if vpc in ('ALIPAY_FIX_CASH_VOUCHER_POINT', 'COUPON', 'ALIPAY_EXCHANGE_AMOUNT_VOUCHER'):
        return '支付红包'

    # WHEN voucher_product_code IN (...) THEN "黑卡权益"
    if vpc in ('ALIPAY_CITEM_MIX_SPE_VOUCHER', 'ALIPAY_COMMON_ITEM_FIX_VOUCHER'):
        return '黑卡权益'

    # WHEN prize_sub_type = "VCP_CASH_PRIZE" THEN "现金红包"
    if pst == 'VCP_CASH_PRIZE':
        return '现金红包'

    # WHEN prize_sub_type = "GOLD_BILL" OR (prize_type = "FINANCE_PRIZE" AND prize_sub_type = "FINANCE_PRIZE" AND voucher_product_code IS NULL) THEN "黄金票"
    if pst == 'GOLD_BILL' or (pt == 'FINANCE_PRIZE' and pst == 'FINANCE_PRIZE' and not vpc):
        return '黄金票'

    # WHEN prize_sub_type = "MONEY_BOX" OR voucher_product_code = "ALIPAY_FIN_TRANS_YEB_COUPON" THEN "余额宝红包"
    if pst == 'MONEY_BOX' or vpc == 'ALIPAY_FIN_TRANS_YEB_COUPON':
        return '余额宝红包'

    # WHEN prize_sub_type IN ("YEB_EXPERIENCE_MONEY", "CASH_PRIZE")
    #   OR (prize_sub_type = "YEB_SWITCH_INTO" AND voucher_product_code = "ALIPAY_FIN_NEW_NOFUND_VOUCHER")
    #   OR (prize_sub_type IN ("FINANCE_PRIZE", "DEDUCT_COUPON") AND voucher_product_code = "ALIPAY_FIN_NEW_NOFUND_VOUCHER"
    #       AND (名称含"体验金"))
    # THEN "余额宝体验金"
    if pst in ('YEB_EXPERIENCE_MONEY', 'CASH_PRIZE'):
        return '余额宝体验金'
    if pst == 'YEB_SWITCH_INTO' and vpc == 'ALIPAY_FIN_NEW_NOFUND_VOUCHER':
        return '余额宝体验金'
    if pst in ('FINANCE_PRIZE', 'DEDUCT_COUPON') and vpc == 'ALIPAY_FIN_NEW_NOFUND_VOUCHER':
        if _name_contains('体验金', prize_name, camp_name, plan_name, voucher_template_name):
            return '余额宝体验金'

    # WHEN prize_sub_type = "LICAI_TIYANJIN" THEN "理财体验金"
    if pst == 'LICAI_TIYANJIN':
        return '理财体验金'

    # WHEN 名称含"免佣券" THEN "其他"
    if _name_contains('免佣券', prize_name, camp_name, plan_name, voucher_template_name):
        return '其他'

    # WHEN prize_sub_type = "AIP_SCHOLARSHIP"
    #   OR (prize_sub_type IN ("FINANCE_PRIZE", "DEDUCT_COUPON") AND 名称含"定投")
    # THEN "定投红包"
    if pst == 'AIP_SCHOLARSHIP':
        return '定投红包'
    if pst in ('FINANCE_PRIZE', 'DEDUCT_COUPON'):
        if _name_contains('定投', prize_name, camp_name, plan_name, voucher_template_name):
            return '定投红包'

    # WHEN prize_sub_type = "ALIPAY_FIN_TRANS_FUND_COUPON"
    #   OR (prize_sub_type = "DEDUCT_COUPON" AND voucher_product_code = "ALIPAY_FIN_TRANS_FUND_COUPON"
    #       AND 名称含"种子"/"份额"/"体验")
    # THEN "份额红包"
    if pst == 'ALIPAY_FIN_TRANS_FUND_COUPON':
        return '份额红包'
    if pst == 'DEDUCT_COUPON' and vpc == 'ALIPAY_FIN_TRANS_FUND_COUPON':
        for kw in ('种子', '份额', '体验'):
            if _name_contains(kw, prize_name, camp_name, plan_name, voucher_template_name):
                return '份额红包'

    # WHEN voucher_product_code = "ALIPAY_FIN_TRANS_FUND_COUPON"
    #   AND (prize_sub_type IS NULL OR prize_sub_type IN ("FINANCE_PRIZE", "DEDUCT_COUPON"))
    # THEN "财运红包"
    if vpc == 'ALIPAY_FIN_TRANS_FUND_COUPON' and (not pst or pst in ('FINANCE_PRIZE', 'DEDUCT_COUPON')):
        return '财运红包'

    # WHEN prize_sub_type = "ALIPAY_FIN_NEW_NOFUND_VOUCHER" THEN "理财券"
    if pst == 'ALIPAY_FIN_NEW_NOFUND_VOUCHER':
        return '理财券'

    return '其他'


def _classify_baoxian(prize_type: str, prize_sub_type: str,
                      voucher_product_code: str, prize_name: str,
                      camp_name: str, plan_name: str,
                      voucher_template_name: str) -> str:
    """保险场景权益分类 - 严格对照语雀文档SQL"""
    vpc = voucher_product_code or ''
    pst = prize_sub_type or ''
    pt = prize_type or ''

    # WHEN voucher_product_code = "COUPON" THEN "支付红包"
    if vpc == 'COUPON':
        return '支付红包'

    # WHEN prize_sub_type = "INSURANCE_PRIZE_COUNT_AMOUNT" THEN "安心豆"
    if pst == 'INSURANCE_PRIZE_COUNT_AMOUNT':
        return '安心豆'

    # WHEN prize_sub_type in ("INS_FREE_GURANTEE_GOLD", "INS_TRANSFER_GURANTEE_GOLD", "INSURANCE_PRIZE_MONEY_AMOUNT") THEN "保障金"
    if pst in ('INS_FREE_GURANTEE_GOLD', 'INS_TRANSFER_GURANTEE_GOLD', 'INSURANCE_PRIZE_MONEY_AMOUNT'):
        return '保障金'

    # WHEN voucher_product_code = "ALI_INS_EXCHANGE_VOUCHER" AND 名称含"保障金"或"健康金" THEN "保障金"
    if vpc == 'ALI_INS_EXCHANGE_VOUCHER':
        for kw in ('保障金', '健康金'):
            if _name_contains(kw, prize_name, camp_name, plan_name, voucher_template_name):
                return '保障金'

    # 新增：INS_BLUE_BEAN|INS_BLUE_BEAN → 安心豆
    if pt == 'INS_BLUE_BEAN' and pst == 'INS_BLUE_BEAN':
        return '安心豆'

    # 新增：FINANCE_PRIZE|GOLD_BILL → 黄金票
    if pt == 'FINANCE_PRIZE' and pst == 'GOLD_BILL':
        return '黄金票'

    # 新增：FINANCE_PRIZE|YEB_SWITCH_INTO → 余额宝红包
    if pt == 'FINANCE_PRIZE' and pst == 'YEB_SWITCH_INTO':
        return '余额宝红包'

    # 新增：VCP_CASH_PRIZE|VCP_CASH_PRIZE → 现金红包
    if pt == 'VCP_CASH_PRIZE' and pst == 'VCP_CASH_PRIZE':
        return '现金红包'

    # 新增：VIRTUAL_PRIZE_MONEY|VIRTUAL_PRIZE_MONEY → 支付红包
    if pt == 'VIRTUAL_PRIZE_MONEY' and pst == 'VIRTUAL_PRIZE_MONEY':
        return '支付红包'

    # 新增：FINANCE_PRIZE|FIN_CERT_VOUCHER → 理财红包
    if pt == 'FINANCE_PRIZE' and pst == 'FIN_CERT_VOUCHER':
        return '理财红包'

    return '其他'


def _classify_xiaojin(prize_type: str, prize_sub_type: str,
                      voucher_product_code: str, prize_name: str,
                      camp_name: str, plan_name: str,
                      voucher_template_name: str) -> str:
    """消金场景权益分类 - 严格对照语雀文档SQL"""
    vpc = voucher_product_code or ''
    pst = prize_sub_type or ''
    pt = prize_type or ''

    # WHEN voucher_product_code = "COUPON" AND 名称含还款/逾期相关 THEN "花借还款红包"
    if vpc == 'COUPON':
        for kw in ('借呗还款', '借呗逾期', '花呗还款', '花呗逾期', '逾期还款'):
            if _name_contains(kw, prize_name, camp_name, plan_name, ''):
                return '花借还款红包'

    # WHEN voucher_product_code = "COUPON" OR voucher_product_code LIKE "ALIPAY%CASH_VOUCHER%" OR prize_sub_type = "COMBINED_PRIZE" THEN "支付红包"
    if vpc == 'COUPON' or ('ALIPAY' in vpc and 'CASH_VOUCHER' in vpc) or pst == 'COMBINED_PRIZE':
        return '支付红包'

    # WHEN voucher_product_code = "DISCOUNT" THEN "立减"
    if vpc == 'DISCOUNT':
        return '立减'

    # WHEN prize_type = "VCP_CASH_PRIZE" THEN "现金红包"
    if pt == 'VCP_CASH_PRIZE':
        return '现金红包'

    # WHEN prize_type = "TMALL_LATOUR2_TRIPLE" AND prize_sub_type = "alipayHongbao" THEN "促开卡红包"
    if pt == 'TMALL_LATOUR2_TRIPLE' and pst == 'alipayHongbao':
        return '促开卡红包'

    # WHEN prize_type = "HUABEI_GOLDEN_POINT" THEN "花呗金"
    if pt == 'HUABEI_GOLDEN_POINT':
        return '花呗金'

    # WHEN voucher_product_code = "ALIPAY_COMMON_FREE_DISCOUNT" AND 名称含"折" THEN "打折券"
    if vpc == 'ALIPAY_COMMON_FREE_DISCOUNT':
        if _name_contains('折', prize_name, camp_name, plan_name, voucher_template_name):
            return '打折券'
        # WHEN voucher_product_code = "ALIPAY_COMMON_FREE_DISCOUNT" THEN "免息券"
        return '免息券'

    # WHEN (prize_sub_type = "CC_CREDIT_BENEFIT" OR (prize_type = "PCREDIT_PRIZE" AND prize_sub_type = "AMOUNT"))
    #   AND voucher_product_code IS NULL AND 名称含"折"
    # THEN "分期收银台打折权益"
    if (pst == 'CC_CREDIT_BENEFIT' or (pt == 'PCREDIT_PRIZE' and pst == 'AMOUNT')) and not vpc:
        if _name_contains('折', prize_name, camp_name, plan_name, voucher_template_name):
            return '分期收银台打折权益'
        # ELSE "分期收银台免息权益"
        return '分期收银台免息权益'

    # 新增：PCREDIT_PRIZE + subType含AMOUNT（如AMOUNT&false&ALL）→ 利率打折卡
    if pt == 'PCREDIT_PRIZE' and 'AMOUNT' in pst:
        return '利率打折卡'

    # 新增：BC_CASH_PACK → 现金红包
    if pt == 'BC_CASH_PACK' and pst == 'BC_CASH_PACK':
        return '现金红包'

    # 新增：WeCash → 现金红包
    if pt == 'WeCash' and pst == 'WeCash':
        return '现金红包'

    # 新增：MYBANK_COUPON_BENEFIT → 还款红包
    if pt == 'MYBANK_COUPON_BENEFIT' and pst == 'MYBANK_COUPON_BENEFIT':
        return '还款红包'

    # 新增：HB_AMOUNT_VOUCHER → 额度券（系数0，借钱要还的）
    if pt == 'HB_AMOUNT_VOUCHER' and pst == 'HB_AMOUNT_VOUCHER':
        return '额度券'

    # 新增：Creditcard_free_quota2 → 免息券
    if pt == 'Creditcard_free_quota2' and pst == 'Creditcard_free_quota2':
        return '免息券'

    # 新增：CERT_CENTER_PRIZE|CERT_CENTER_PRIZE_MONEY + 名称含"神券" → 场景红包
    if pt == 'CERT_CENTER_PRIZE' and pst == 'CERT_CENTER_PRIZE_MONEY':
        if _name_contains('神券', prize_name, camp_name, plan_name, voucher_template_name):
            return '场景红包'

    # 新增：场景红包类（各种三方权益，系数1）
    _scene_hongbao_types = {
        ('AMAP_DC_COUPON', 'AMAP_DC_COUPON'),
        ('ELE_RIGHT', 'ELE_RIGHT'),
        ('HELLO_BIKE_PRIZE', 'RDISCOUNT_VOUCHER'),
        ('TAOBAO_FILM_PRIZE', 'TAOBAO_FILM_PRIZE'),
        ('FLIGGY_RIGHT_PACKAGE_2', 'FLIGGY_RIGHT_PACKAGE_2'),
        ('YOUKU_PRIZE', 'YOUKU_PRIZE'),
        ('TMALL_LATOUR2_TRIPLE', 'fpRedEnvelope'),
        ('TMALL_LATOUR2_TRIPLE', 'plCoupon'),
        ('TMALL_LATOUR2_TRIPLE', 'platformMaochaoCard'),
    }
    if (pt, pst) in _scene_hongbao_types:
        return '场景红包'

    return '其他'


def _classify_wangshang(prize_type: str, prize_sub_type: str,
                        voucher_product_code: str, prize_name: str,
                        camp_name: str, plan_name: str,
                        voucher_template_name: str) -> str:
    """网商场景权益分类 - 严格对照语雀文档SQL

    注意：网商场景SQL使用 benefit_desc 和 benefit_type 字段，
    这些字段在当前数据中不直接可用，需要用已有字段近似映射。
    网商场景较为复杂，此处基于名称关键词做近似分类。
    """
    vpc = voucher_product_code or ''

    # 基于名称关键词近似分类
    all_names = f"{prize_name}|{camp_name}|{plan_name}|{voucher_template_name}"

    if '福利金' in all_names:
        return '福利金'
    if '免息额度' in all_names or '免费额度' in all_names:
        return '免息券'
    if '体验金' in all_names:
        return '体验金'
    if '打折' in all_names:
        return '利率打折卡'
    if '提前收款' in all_names:
        return '提前收款卡'
    if '加息' in all_names:
        return '加息券'

    # 基于 voucher_product_code 判断
    if vpc in ('COUPON', 'ALIPAY_FIX_CASH_VOUCHER_POINT'):
        return '支付红包'

    if vpc == 'DISCOUNT':
        return '立减'

    return '其他'


def _is_physical_item_by_keyword(prize_name: str) -> bool:
    """通过关键词判断是否为实物奖品（金+克/g/mg 组合）"""
    if not prize_name:
        return False
    if '实物' in prize_name or '财富APP排行榜' in prize_name or '克黄金' in prize_name:
        return True
    # 同时含"金"和"克/g/mg"
    if '金' in prize_name and re.search(r'(?:\d+)?(?:克|g|mg)', prize_name, re.IGNORECASE):
        return True
    return False


# 凭证类关键词
_CERT_KEYWORDS = ('凭证', '次数', '机会', '1次', '一次', '谢谢参与', '财神', '报名', '校验')


# ============================================================================
# 大模型兜底分类
# ============================================================================

# 缓存：奖品名 → 大模型判断的类别
_llm_cache: dict = {}


def _llm_classify_prize(prize_name: str, candidates: list) -> str:
    """
    调用大模型判断奖品名属于哪个类别（关键词未命中时的兜底）。

    Args:
        prize_name: 奖品名称
        candidates: 候选类别列表，如 ['实物奖品', '凭证', '其他']

    Returns:
        匹配的类别名，失败返回 '其他'
    """
    if not prize_name:
        return '其他'

    cache_key = f"{prize_name}|{'|'.join(candidates)}"
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    try:
        import sys, os
        _project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..')
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        from glm_client import query_glm

        candidates_str = '、'.join(candidates)
        prompt = f"""判断以下奖品名称属于哪个类别，只回答类别名，不要解释。

类别说明：
- 实物奖品：雨伞、马克杯、吊坠、手表、手机、金条、金饰、茶具等实体物品
- 凭证：吉祥话（如恭喜发财、万事如意、马到成功、金玉满堂等祝福语）、谢谢参与、抽奖机会、报名凭证等无实际价值的奖品
- 其他：不属于上述两类的奖品

可选类别：{candidates_str}
奖品名称：{prize_name}
类别："""

        result = query_glm(prompt, model='glm-5', temperature=0.0, max_tokens=20)
        result = result.strip()

        for c in candidates:
            if c in result:
                _llm_cache[cache_key] = c
                return c

        _llm_cache[cache_key] = '其他'
        return '其他'
    except Exception:
        _llm_cache[cache_key] = '其他'
        return '其他'


def _classify_common(prize_type: str, prize_sub_type: str,
                     prize_name: str) -> str:
    """
    无场景兜底分类器 — 在所有场景分类器返回'其他'后调用。

    第1层：prizeType 关键词/直接映射
    第2层：prizeType+prizeSubType 组合 + 奖品名关键词规则
    """
    pt = prize_type or ''
    pst = prize_sub_type or ''
    pt_lower = pt.lower().replace('_', '')

    # ============================================================
    # 第1层：prizeType 直接映射（不需要看场景和奖品名）
    # ============================================================

    # prizeType 含 antfarm/antforest → 虚拟奖品（忽略大小写和下划线）
    if 'antfarm' in pt_lower or 'antforest' in pt_lower:
        return '虚拟奖品'

    # 固定 prizeType 映射
    _DIRECT_MAP = {
        ('NFT', 'NFT'): '数字藏品',
        ('Gift_Template', 'Gift_Template'): '虚拟奖品',
        ('HTDC_INCR_TIMES_BENEFIT', 'HTDC_INCR_TIMES_BENEFIT'): '虚拟奖品',
        ('consume_challenge_skin', 'consume_challenge_skin'): '虚拟奖品',
        ('skincenterdisplay', 'skincenterdisplay'): '虚拟奖品',
        ('FUCARD', 'FUCARD'): '福卡',
        ('CUSTOM_POINT_CORE_PRIZE', 'CUSTOM_POINT_CORE_PRIZE'): '绿色经营金币',
        ('OVERSEA_TRAVEL_RATE_PRIZE', 'OVERSEA_TRAVEL_RATE_PRIZE'): '汇率优惠券',
        ('COMBINED_PRIZE', 'COMBINED_PRIZE'): '组合奖品',
        ('MYBK_BENEFIT_POINT', 'MYBK_BENEFIT_POINT'): '网商积分奖品',
        ('ANT_MEMBER_POINT_PRIZE', 'ANT_MEMBER_POINT_PRIZE'): '积分',
        ('new_member_point_cert', 'new_member_point_cert'): '积分',
    }
    if (pt, pst) in _DIRECT_MAP:
        return _DIRECT_MAP[(pt, pst)]

    # ============================================================
    # 第2层：关键词规则组合（顺序有先后）
    # ============================================================

    # --- CERT_PRIZE|CERT_PRIZE ---
    if pt == 'CERT_PRIZE' and pst == 'CERT_PRIZE':
        if '黄金票' in prize_name:
            return '黄金票'
        for kw in _CERT_KEYWORDS:
            if kw in prize_name:
                return '凭证'
        if _is_physical_item_by_keyword(prize_name):
            return '实物奖品'
        # 大模型兜底：判断吉祥话/实物
        return _llm_classify_prize(prize_name, ['凭证', '实物奖品', '其他'])

    # --- VIRTUAL_PRIZE|VIRTUAL_PRIZE ---
    if pt == 'VIRTUAL_PRIZE' and pst == 'VIRTUAL_PRIZE':
        if '黄金票' in prize_name:
            return '黄金票'
        if '数币元宝' in prize_name:
            return '数币元宝'
        if '卡' in prize_name and not re.search(r'利率|折扣|卡包', prize_name):
            return '会员权益'
        for kw in _CERT_KEYWORDS:
            if kw in prize_name:
                return '凭证'
        if _is_physical_item_by_keyword(prize_name):
            return '实物奖品'
        # 大模型兜底：判断吉祥话/实物
        return _llm_classify_prize(prize_name, ['凭证', '实物奖品', '其他'])

    # --- MYBK_BENEFIT_VOUCHER|MYBK_BENEFIT_VOUCHER ---
    if pt == 'MYBK_BENEFIT_VOUCHER' and pst == 'MYBK_BENEFIT_VOUCHER':
        if '免息' in prize_name:
            return '免息券'
        if '红包' in prize_name or '利宝' in prize_name:
            return '网商账户红包'
        if '卡' in prize_name and not re.search(r'利率|折扣|卡包', prize_name):
            return '网商会员权益'
        if re.search(r'利率|折扣|期|价|一口价|整笔', prize_name):
            return '利率打折卡'
        if _is_physical_item_by_keyword(prize_name):
            return '实物奖品'
        # 大模型兜底：判断实物
        return _llm_classify_prize(prize_name, ['实物奖品', '其他'])

    # --- FINANCE_PRIZE|DEDUCT_COUPON（无 voucherProductCode 的情况）---
    if pt == 'FINANCE_PRIZE' and pst == 'DEDUCT_COUPON':
        if '股票体验金' in prize_name:
            return '股票体验金'
        if '增利宝体验金' in prize_name:
            return '增利宝体验金'
        if '体验金' in prize_name:
            return '其他体验金'
        return '财运红包'

    # --- FINANCE_PRIZE|FIN_CERT_VOUCHER ---
    if pt == 'FINANCE_PRIZE' and pst == 'FIN_CERT_VOUCHER':
        return '理财红包'

    # --- FINANCE_PRIZE|YEB_SWITCH_INTO ---
    if pt == 'FINANCE_PRIZE' and pst == 'YEB_SWITCH_INTO':
        return '余额宝红包'

    # --- FINANCE_PRIZE|MONEY_BOX ---
    if pt == 'FINANCE_PRIZE' and pst == 'MONEY_BOX':
        return '余额宝红包'

    # --- VCP_CASH_PRIZE ---
    if pt == 'VCP_CASH_PRIZE' and pst == 'VCP_CASH_PRIZE':
        return '现金红包'

    # --- VIRTUAL_PRIZE_MONEY ---
    if pt == 'VIRTUAL_PRIZE_MONEY' and pst == 'VIRTUAL_PRIZE_MONEY':
        return '支付红包'

    # --- 场景红包类（三方权益）---
    _COMMON_SCENE_HONGBAO = {
        ('AMAP_DC_COUPON', 'AMAP_DC_COUPON'),
        ('ELE_RIGHT', 'ELE_RIGHT'),
        ('HELLO_BIKE_PRIZE', 'RDISCOUNT_VOUCHER'),
        ('TAOBAO_FILM_PRIZE', 'TAOBAO_FILM_PRIZE'),
        ('FLIGGY_RIGHT_PACKAGE_2', 'FLIGGY_RIGHT_PACKAGE_2'),
        ('YOUKU_PRIZE', 'YOUKU_PRIZE'),
        ('TMALL_LATOUR2_TRIPLE', 'fpRedEnvelope'),
        ('TMALL_LATOUR2_TRIPLE', 'plCoupon'),
        ('TMALL_LATOUR2_TRIPLE', 'platformMaochaoCard'),
    }
    if (pt, pst) in _COMMON_SCENE_HONGBAO:
        return '场景红包'

    # --- 券类（VOUCHER_PRIZE 系列）---
    _VOUCHER_MAP = {
        'ALIPAY_COMMON_ITEM_FIX_VOUCHER': '代金券',
        'ALIPAY_FIX_CASHLESS_VOUCHER': '代金券',
        'ALIPAY_IMPORT_FIX_VOUCHER': '代金券',
        'ALIPAY_COMMON_ITEM_DST_VOUCHER': '折扣券',
        'ALIPAY_DST_CASHLESS_VOUCHER': '折扣券',
        'ALIPAY_COMMON_ITEM_SPE_VOUCHER': '渠道券',
        'ALIPAY_BIZ_EXCHANGE_VOUCHER': '兑换券',
        'ALIPAY_IMPORT_EXCHANGE_VOUCHER': '兑换券',
        'ALIPAY_FIN_NEW_NOFUND_VOUCHER': '兑换券',
        'ALI_INS_EXCHANGE_VOUCHER': '兑换券',
        'ALIPAY_INS_NEW_EXCHANGE_VOUCHER': '兑换券',
        'ALIPAY_PERIODIC_CASH_VOUCHER': '现金红包',
        'ALIPAY_RANDOM_CASH_VOUCHER': '现金红包',
        'ALIPAY_INTFREE_PAY_PLATFORM': '免息券',
        'COUPON': '支付红包',
    }
    if pt == 'VOUCHER_PRIZE' and pst in _VOUCHER_MAP:
        return _VOUCHER_MAP[pst]

    # --- 其他消金类 ---
    if pt == 'BC_CASH_PACK' and pst == 'BC_CASH_PACK':
        return '现金红包'
    if pt == 'WeCash' and pst == 'WeCash':
        return '现金红包'
    if pt == 'MYBANK_COUPON_BENEFIT' and pst == 'MYBANK_COUPON_BENEFIT':
        return '还款红包'
    if pt == 'MYBK_BENEFIT_COUPON' and pst == 'MYBK_BENEFIT_COUPON':
        return '网商账户红包'
    if pt == 'HB_AMOUNT_VOUCHER' and pst == 'HB_AMOUNT_VOUCHER':
        return '额度券'
    if pt == 'Creditcard_free_quota2' and pst == 'Creditcard_free_quota2':
        return '免息券'
    if pt == 'INS_BLUE_BEAN' and pst == 'INS_BLUE_BEAN':
        return '安心豆'

    # --- TMALL_LATOUR2_TRIPLE|interactItemCoupon → 需要判断是否实物 ---
    if pt == 'TMALL_LATOUR2_TRIPLE' and pst == 'interactItemCoupon':
        if _is_physical_item_by_keyword(prize_name):
            return '实物奖品'
        # 大模型兜底：判断实物
        return _llm_classify_prize(prize_name, ['实物奖品', '其他'])

    # --- CERT_CENTER_PRIZE + 神券 → 场景红包 ---
    if pt == 'CERT_CENTER_PRIZE' and pst == 'CERT_CENTER_PRIZE_MONEY':
        if '神券' in prize_name:
            return '场景红包'

    return '其他'


def classify_prize_benefit(scenarios: list, prize_type: str, prize_sub_type: str,
                           voucher_product_code: str, prize_name: str,
                           camp_name: str, plan_name: str,
                           voucher_template_name: str) -> str:
    """
    根据业务场景和奖品属性，分类权益类型。

    严格对照语雀文档《非红包权益价值量化词典》中的SQL逻辑。
    不同场景使用不同的CASE WHEN规则。
    若活动命中多个场景，按优先级取第一个有效分类。

    Args:
        scenarios: 业务场景列表（如 ['财富'], ['消金', '财富']）
        其余参数: 奖品属性字段

    Returns:
        权益类型字符串（如 "黄金票"、"余额宝体验金"、"花呗金"等）
    """
    classifier_map = {
        '财富': _classify_caifu,
        '保险': _classify_baoxian,
        '消金': _classify_xiaojin,
        '网商': _classify_wangshang,
    }

    for scenario in scenarios:
        classifier = classifier_map.get(scenario)
        if not classifier:
            continue
        result = classifier(prize_type, prize_sub_type, voucher_product_code,
                           prize_name, camp_name, plan_name, voucher_template_name)
        if result != '其他':
            return result

    # 场景分类器全部返回'其他'，走无场景兜底分类器
    common_result = _classify_common(prize_type, prize_sub_type, prize_name)
    if common_result != '其他':
        return common_result

    if scenarios:
        return '其他'
    return '未识别'


# ============================================================================
# Pipeline enrich 接口
# ============================================================================

def enrich(data):
    """
    Pipeline enrichment: 为每个奖品计算权益类型和真实价值。

    从 data_preprocessing.processor 调用 extract_prize_values_from_data,
    将结果写入 data['prizes'] 各元素和 data['prize_values']。

    添加字段:
    - prizes[i].benefit_type, true_value, value_level, value_desc
    - prize_values: dict  {prize_id: {prize_name, benefit_type, true_value, ...}}
    """
    if data is None or data.get('_parse_failed'):
        data['prize_values'] = {}
        return data

    import os, sys
    _skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _SANDBOX_WS = "/home/admin/.openclaw/workspace"
    _skill_dirs = [_skill_dir]
    _bn = os.path.basename(_skill_dir.rstrip('/'))
    if _bn == 'skills-local':
        _sib = os.path.join(os.path.dirname(_skill_dir), 'skills')
    elif _bn == 'skills':
        _sib = os.path.join(os.path.dirname(_skill_dir), 'skills-local')
    else:
        _sib = None
    if _sib and os.path.isdir(_sib) and _sib not in _skill_dirs:
        _skill_dirs.append(_sib)
    for _sd in _skill_dirs:
        if _sd not in sys.path:
            sys.path.insert(0, _sd)
    from data_preprocessing.processor import extract_prize_values_from_data

    raw_data = data.get('_raw_data')
    if raw_data is None:
        data['prize_values'] = {}
        return data

    scenarios = data.get('scenarios', [])
    camp_name = data.get('camp_name', '') or ''
    plan_name = data.get('plan_name', '') or ''

    prize_values = extract_prize_values_from_data(raw_data, scenarios, camp_name, plan_name)
    data['prize_values'] = prize_values

    # Enrich each prize in data['prizes'] with value info + threshold
    for p in data.get('prizes', []):
        prize_id = p.get('prize_id', '')
        if prize_id in prize_values:
            pv = prize_values[prize_id]
            p['benefit_type'] = pv.get('benefit_type', '')
            p['true_value'] = pv.get('true_value', 0)
            p['value_level'] = pv.get('value_level', '')
            p['value_desc'] = pv.get('value_desc', '')
        # 提取门槛字段
        pname = p.get('prize_name', '')
        p['threshold'] = _extract_prize_threshold(pname)
        # 同步到 prize_values
        if prize_id in prize_values:
            prize_values[prize_id]['threshold'] = p['threshold']

    return data


def _extract_prize_threshold(prize_name):
    """
    从奖品名提取门槛金额（元）

    匹配模式：满1000减N / 满1000元 / N元门槛 / 百5（=100元中给5元）
    复杂情况（两个数字、更复杂表述）后续接大模型 TODO

    Returns: float, 0表示未识别
    """
    import re
    if not prize_name:
        return 0

    # "满N减" / "满N元"
    m = re.search(r'满(\d+(?:\.\d+)?)\s*[元减]', prize_name)
    if m:
        return float(m.group(1))

    # "N元门槛"
    m = re.search(r'(\d+(?:\.\d+)?)\s*元门槛', prize_name)
    if m:
        return float(m.group(1))

    # "百N" = 100元门槛（如"百5"=100元给5元）
    m = re.search(r'百(\d+(?:\.\d+)?)', prize_name)
    if m:
        return 100

    # 大模型兜底：从奖品名提取门槛
    return _llm_extract_threshold(prize_name)


# 门槛提取缓存
_threshold_cache: dict = {}


def _llm_extract_threshold(prize_name: str) -> float:
    """
    大模型提取门槛金额

    处理复杂情况：两个数字、"百5"变体、"满XX用"等
    """
    if not prize_name:
        return 0

    if prize_name in _threshold_cache:
        return _threshold_cache[prize_name]

    try:
        import sys, os
        _project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..')
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        from glm_client import query_glm

        prompt = """从以下奖品名称中提取"使用门槛"金额（元）。
门槛是指用户需要消费/申购/支付多少钱才能使用这个奖品。
如果没有门槛信息，回答0。只回答数字，不要解释。

示例：
- "满1000减20元红包" → 1000
- "百5红包" → 100（百=100元）
- "3元支付红包" → 0（这是红包面额，不是门槛）
- "申购10000元得2元红包" → 10000

奖品名称：{}
门槛金额（元）：""".format(prize_name)

        result = query_glm(prompt, model='glm-5', temperature=0.0, max_tokens=20)
        result = result.strip()

        import re as _re
        m = _re.search(r'(\d+(?:\.\d+)?)', result)
        if m:
            val = float(m.group(1))
            _threshold_cache[prize_name] = val
            return val
    except Exception:
        pass

    _threshold_cache[prize_name] = 0
    return 0

