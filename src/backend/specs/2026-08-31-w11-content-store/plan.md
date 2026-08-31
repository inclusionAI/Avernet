# W11 plan — 拆解与顺序

> 单人;每组件测试先行(RED→GREEN);CI 全绿后提交;提交后复跑 `ci_test.sh` 取真实变行覆盖。
> 与 W2/W3 同法:spec 目录三件套、单一 feat 分支(`feat/bot-config-manifest-w11-arc`,源自 origin/dev)、
> 全绿后一次批量终审。

1. **骨架与 DDL** — `content/` 包空壳 + `sql/2026_08_31_manifest_content.sql`(头注释写全决策)
   + ORM model(`content/models.py`,tenant guard、AutoIncrementBigInteger、
   MEDIUMTEXT 级列宽注意)+ `core/schema.py` 登记。
2. **仓储(TDD)** — protocol(`protocols/bot/manifest_content.py`)+ 实现
   (`implementations/bot/manifest_content.py`);真库测试
   `tests/community/repository/bot/test_manifest_content_repository.py`
   (沿用 W1 仓储测试的 fixture 形态):add/逐 bot 查询/append-only/tenant 隔离。
3. **内容存储服务(TDD)** — `service.py`+`service_protocol.py`+`errors.py`:
   store(重算哈希守门、原子写、去重、行落库)、read(流式校验、缺失吵闹)、
   records;`content/test_content_store.py` 安全矩阵(去 query/userinfo、凭证名不带值、
   坏块/缺块、并发同 digest 幂等、目录分片形态)。
4. **配置与登记** — application.yaml 键 `bot_config_manifest.content_store_dir`(中性默认)+
   `content/settings.py` 纯解析(缺省/覆写/坏类型)+ golden 三快照重生 +
   flow_coverage 条目文字补 W11 + 模块 README 的 W11 节与 Context Boundary。
5. **验证交付** — 全量 `ci_test.sh --base origin/dev`;批量终审意见落回;提交(单一 feat commit)→ PR。
