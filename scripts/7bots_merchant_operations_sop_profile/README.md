# 商家经营目标到 SOP 协作队

该 profile 对应商家经营目标串行模板。`bots.json` 中的 `name` 使用当前已提供的 Bot 标识；正式协同 YAML 仍通过 participant slot 绑定，不把 Bot ID 写入 seed。

关键字识别依赖 manifest 的 `scopes`、每个 Bot 的 `domains` 与 `skills`：先命中商家/门店场景，再命中经营目标或问题词。明确库存、采购、交期时优先供应履约；明确投诉、差评、舆情时优先舆情；普通经营目标按模板主链从店主侧开始。
