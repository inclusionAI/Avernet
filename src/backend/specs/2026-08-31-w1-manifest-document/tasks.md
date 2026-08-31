# W1 tasks

- [x] A.0 spec 目录、模块骨架(`core/bot_config_manifest/{README,__init__}`)、feat 分支
- [x] A.1 DDL `ac_bot_config_manifest`(uk tenant+manifest_key)+ ORM(租户守卫、长度前缀代理键)
- [x] A.2 仓储测试(11 条:键隔离、整份替换、原文往返、删除幂等、流经代理索引)
- [x] A.3 仓储协议/实现(并发重试一次、func.now() 审计戳)
- [x] A.4 `manifest_schema.py`(六类目模型、规则码表、`parse_document`/`validate_document`)
- [x] A.5 校验矩阵测试(41 条:每规则码负例+黄金文档+往返)
- [x] A.6 `capabilities.py`(二维单函数、fail closed、identity 白名单引用不复制)+ 测试(16)
- [x] A.7 `feature_flags.py`(BCM_API_ENABLED,进程缓存+测试重置)
- [x] A.8 `manifest_service_protocol.py`+服务+`api/` re-export+DI 绑定+`_PAIRS`+服务测试(15,真 SQLite)
- [x] A.9 4 路由+ADMISSION 4 行+AUTHORIZATION 4 行+`config_manifest_support.py`+ errors/responses 映射(422 带 violations、404 dark)
- [x] A.10 `bots.openapi.json` 手工增量(+2 path/+15 schema,语义零改既有)+ endpoint-framework 用例(8)
- [x] A.11 适配层用例(11,真 SQLite+StaticPool:跨线程共享内存库)+ 共享 router 回归(140 全绿,含 d8a909ce 的 aicoding 用例)
- [x] A.12 `ci_test.sh` 全量(15931 passed / line 88.10% / **changed-line 100% 61/61**)→ 批量终审(3 HIGH 已修:coverage-gap 2 用例、exclude_defaults 空类目语义、lone-surrogate 500)→ 提交(无 attribution)→ PR

## 遗留(显式)

- W10(#1509)催收 comment:范围假设(10 项单人)未获用户当面确认,未发外部沟通。
- bot 删除不清理 manifest 行、PUT 无 #935 式 withdraw 防线(键稳定性使行不可继
  承、无执行面;终审 M3 记为一致性缺口——W4/W5 接线时补 purge+withdraw 或
  显式豁免)。
- admission.py / responses.py 因四行增量过千行帽:已按仓库惯例进
  `_ALLOWLIST`(带 per-group 模块化 follow-up;新组错误行已按 errors_manifest
  模块合并模式落地)。
