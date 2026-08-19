# final_res 输出字段定义

对齐「数金一页纸内容」文档，pipeline 输出共 39 个字段。

## 一、基础信息（18个）

| 字段 | 来源 | 说明 |
|------|------|------|
| 活动名称 | event_property | 活动名称 |
| 活动CP号 | event_property | 活动CP编码 |
| 方案名称 | event_property | 方案名称 |
| 方案PL号 | event_property | 方案PL编码 |
| 方案背景 | event_property | 方案描述/背景 |
| 对用户展示规则 | event_property | 展示规则 |
| 业务线 | event_property | 业务线名称 |
| 出资方 | event_property | 蚂蚁集团/商户/第三方 |
| 方案预算 | event_property | 预算金额 |
| 活动开始时间 | event_property | GMT开始（YYYY-MM-DD HH:mm:ss） |
| 活动结束时间 | event_property | GMT结束（YYYY-MM-DD HH:mm:ss） |
| 方案开始时间 | event_property | PL开始 |
| 方案结束时间 | event_property | PL结束 |
| 活动需求方 | event_property | 需求方 |
| 活动创建人 | event_property | 创建人 |
| 活动来源 | event_property | sub_biz_type映射：lotteryCamp→自动抽奖，directCamp→直发，taskCamp→任务 |
| 奖品散点数据 | pipeline | 奖品价值散点图数据列表 |
| 证件号为空是否默认通过 | pipeline | id_card_pass → "默认通过"/"默认不通过" |

## 二、活动限制（11个）

| 字段 | 来源 | 格式 | 说明 |
|------|------|------|------|
| 活动类型 | pipeline | 字符串 | is_dapro+environment → "大促"/"日常活动"/"预发-大促"/"预发-日常活动" |
| 获取限制 | pipeline | 字符串 | crowdLimitType映射：notLimit→无限制，CROWDRULEID→按人群规则限制，COMMONCONFIG→限制用户实名 |
| 是否限制实名 | pipeline | "是"/"否" | realNameAuth != 0 则为"是" |
| 是否限制四同 | pipeline | 字符串 | 格式："通过。原因" 或 "不通过。原因"（由 config_checks.frequency_control 决定） |
| 是否地域限制 | pipeline | "是"/"未知" | lbsLimit != NO_LIMIT 则为"是" |
| 参与活动次数限制 | pipeline | 字符串 | 活动粒度终身次数限制，如"同支付宝账户终身限1次"、"无限制" |
| 参与活动频次限制 | pipeline | 字符串 | 活动粒度周期频次限制，如"同手机号每天限10次"、"无限制" |
| 活动中奖次数限制 | pipeline | 字符串 | 奖品粒度终身次数限制 |
| 活动中奖频次限制 | pipeline | 字符串 | 奖品粒度周期频次限制 |
| 活动风控方案 | pipeline | 字符串 | "咨询实时风控"/"咨询离线风控"/"不咨询风控" |
| 是否所有奖品都限制了四同 | pipeline | "是"/"否" | 所有奖品的 dim_count >= 4 则为"是" |

## 三、奖品与激励（2个）

| 字段 | 来源 | 格式 | 说明 |
|------|------|------|------|
| 奖品配置信息 | pipeline | 列表 | 每个元素格式：`"{prize_id},{prize_name}"` |
| 单用户单日激励金额期望值 | pipeline | 字符串 | 如 "0.5元"、"0元" |

## 四、评审结论（8个）

| 字段 | 来源 | 格式 | 说明 |
|------|------|------|------|
| 活动配置是否合理 | pipeline | 字符串 | "有配置风险" / "无配置风险" |
| 活动配置是否合理原因 | pipeline | 字符串 | 各项不通过原因用";"连接;通过时为"配置校验通过" |
| 详细校验结果 | pipeline | 列表 | 每项: {rule_name, status, reason} |
| 是否有风险 | pipeline | "是"/"否" | has_config_risk OR has_biz_risk |
| 风险判断原因 | pipeline | 字符串 | 分"配置风险"和"业务风险"两类,用";"连接 |
| 防控模块推荐 | pipeline+LLM | 列表 | LLM推荐防控模块,默认空列表 |
| 防控模块推荐原因 | pipeline+LLM | 字符串 | LLM推荐原因,默认空字符串 |
| 感知模块推荐 | pipeline+LLM | 列表 | LLM推荐感知模块,默认空列表 |

## 五、LLM增强字段（10个）

| 字段 | 来源 | 格式 | 说明 |
|------|------|------|------|
| 活动目标 | preprocess+LLM | 字符串 | 优先从event_property提取goal/bizTarget,LLM可补充推断 |
| 活动玩法 | LLM | 字符串 | LLM根据活动信息描述核心玩法 |
| 活动关键词 | LLM | 列表 | LLM提取活动关键词 |
| 目标和玩法的匹配情况 | LLM | "匹配"/"不匹配" | LLM判断活动玩法能否达成目标 |
| 目标和玩法是否匹配的原因 | LLM | 字符串 | LLM分析匹配/不匹配的原因 |
| 玩法和激励的匹配情况 | LLM | "匹配"/"不匹配" | LLM判断激励类型是否与玩法匹配 |
| 玩法和激励是否匹配的原因 | LLM | 字符串 | LLM分析匹配/不匹配的原因 |
| 评审备注 | LLM | 字符串 | LLM总结活动主要风险点和合规情况 |
| 单用户单日获奖次数上限 | pipeline | 字符串 | 从频次限制模型计算,如"同支付宝账户每天限3次"、"无限制" |