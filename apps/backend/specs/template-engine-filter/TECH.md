# ac_templates 单条查询引擎过滤

## 最终范围

仅 TemplateRepository.get_by_bot_id 增加引擎预检：按 bot_id、可选 owner_id、
当前 env 和 is_delete=0 检查 ac_bots.active_engine，只有 claude_code / aicoding
继续查询 ac_templates；其他引擎或 Bot 不存在返回 None，不访问模板表。

list_by_bot_ids、list_by_architect_bot_id、exists_by_bot_id 以及新增、更新、删除
全部保持原行为，不增加引擎检查。撤回之前的 exists_record_by_bot_id 及其调用。

## 调用链与契约

- get_by_bot_id 新增 keyword-only owner_id，默认 None 保留无 owner 上下文的历史调用。
- TemplateService.get_template 透传 owner_id；BotService.get_bot 显式传入其
  原本用于 owner 校验的 user_id，避免不同 owner 的 default Bot 互相影响。
- get_template_config / get_template_config_strict 等单条读取经 get_by_bot_id
  生效；Repository 与 strict 读取仍上抛查询异常，宽松读取沿用原来的空值降级。
- ac_templates 无 owner_id 字段且 bot_id 唯一，本次只给 ac_bots 预检加 owner
  条件，不改变模板表结构。不传 owner 的单条调用仍不限定 owner。
- 不改变写入流程及错误处理，不修改配置、HTTP 入参和部署依赖。

## 验证策略

SQLite ORM 和 SQL 监听验证允许/拒绝引擎、Bot 缺失、软删除、环境隔离、
引擎切换、两个 owner 共用 default、模板缺失、异常语义。
验证批量/关联/存在性查询无引擎限制且不访问 ac_bots；验证非目标引擎的
模板新增、修改、删除和 upsert 保持有效。
运行既有模板服务、Gateway 契约、相关端点及 Repository 架构检查。

## 文件与风险

bot_service.py 现有 6,249 行，已在原有大小门禁 allowlist 中。本次只替换一行
参数传递，不扩大 allowlist；按职责拆分留待独立重构。
单条查询多一次 ac_bots 预检，未承诺跨语句引擎切换的原子性。
遵守 docs/arch/arch.rules.md 等现有架构规范。

## 最终验证结果

- 单条查询、模板 Service、Bot 详情、Gateway 契约、Repository 架构及大小门禁：86 项通过。
- Endpoint runner 定向 appcoding / architect-rebind / admin：61 项通过。
- 静态未定义/未使用导入检查、语法检查和 git diff --check 通过。
- 未执行 Backend 全量测试及真实 OceanBase 集成测试。
