# TC 双实例迁移与离线工具

## 服务入口与读写边界

旧实例继续运行旧版本并读写旧 Worker/Profile/向量表。新实例运行本次 TC 改造版本，
所有查询和写入都使用新 Card/Doc 与独立 Qdrant 集合；没有旧表混合读取或应用内切源开关。
新实例必须使用独立部署、独立地址，导入完成前不加入原业务入口的负载均衡。

“旧接口”在这里指业务系统现有的服务入口；“新接口”指新实例的独立入口。
路径、请求和响应仍可使用原 sync、online/offline、availability、config、search、recommend、Fuse。
不新增一套请求格式或逐请求迁移参数。读写目标由入口指向的实例决定：

| 阶段 | 原业务入口：查询 | 原业务入口：写入/更新 | 新实例独立入口 |
| --- | --- | --- | --- |
| 切流前 | 旧实例读取旧表/旧索引 | 旧实例更新旧表/旧索引 | 查询和更新均使用新表/新索引 |
| 切流后 | 新实例读取新表/新索引 | 新实例更新新表/新索引 | 查询和更新仍使用新表/新索引 |

## 迁移与切流

1. 确认两张 TC 新表、权限、部署版本和模型配置；将新版本部署到独立地址。
   TC 新 Qdrant 从新 Doc 准备，旧实例的表和集合不变。
2. 正常业务继续调用旧入口，旧实例持续更新旧数据。迁移脚本从权威旧资料读取，
   对新入口调用原 sync 导入当前资料，调用 config/状态接口补齐配置与状态。
   画像/历史按既定保留范围归档，不从 CSV 默认值覆盖当前状态。
3. 在新入口验证旧查询接口、过滤、配置和新 Card/Doc 数量与版本。
   `/ready=200` 只证明当前实例索引就绪，不证明全量迁移完成。
4. 短暂停止原业务的全部写入口并排空旧后台任务，确定性重读、重放及核对最后的
   资料、配置、状态和删除。不依赖未经验证的修改时间或增量日志。
5. 新索引及旧查询接口验收完成后，将原业务入口整体切到新实例；search/recommend/Fuse
   和 sync/online/availability/config 必须一并切流，不能只切查询或只切写入。
   排空旧连接与旧后台任务，确认所有调用方均进入新实例，再恢复业务写入。

新实例没有 `legacy/card` 模式。旧部署配置中试用过的 `BCSFUSE_TC_INDEX_SOURCE` / `tc.index_source`
应移除，不能用它改变新实例的读写目标。切换发生在服务路由/部署入口，不在应用内切换表。
不做双写；新业务写入开始后，回旧实例前仍需停写并反向补齐新增、更新、删除及配置。
融合历史沿用原有权威存储，首次部署和回退均须确认环境及连接配置一致。

## 离线工具

工具读取用户提供的 `bcs_bots` CSV，默认只读盘点；只有显式传入 `--sqlite` 才写入隔离的本地库。
它不访问部署数据库、不调用模型、不构建 Doc / 索引，也不切换运行中的应用。
CSV 只有基础信息，不能据此声称配置、历史画像、会话和所有运行状态已完成全量迁移。

从仓库根目录运行：

```bash
python apps/bcsfuse/scripts/migrate_tc_cards.py /path/to/bcs_bots.csv --environment pre

python apps/bcsfuse/scripts/migrate_tc_cards.py /path/to/bcs_bots.csv \
  --environment pre --sqlite /path/to/isolated-tc.sqlite \
  --legacy-jsonl /path/to/selected-legacy.jsonl \
  --archive /path/to/migration-audit.jsonl
```

`--environment` 必须明确；其他环境计入 `skipped_environment`，不修改该环境的库。
CLI 显式使用 `bcsfuse_tc_agent_cards` 与 `tc_search_index_documents`，不写支付宝默认的
`bcsfuse_agent_cards / search_index_documents`。Card 和 Doc 同时分表，删除 TC 资料不会退役支付宝 Doc。
同一 SQLite 文件中的不同环境由公共 catalog 按环境隔离；TC 使用独立源表及独立索引空间，
不能只凭数据源别名推断物理数据库分离。部署注入仓储也须明确选择这两张 TC 表。
`--archive` 使用新文件且权限为 `0600`，已有文件会报错，避免覆盖证据。
SQLite 新文件权限为 `0600`。原始 CSV、补充 JSONL、归档和本地数据库均不得提交到仓库。

## 字段与单资料选择

| 输入 | Card / 处理 |
| --- | --- |
| `bot_uuid` | 完整 `agent_id`，不拆冒号、不重造 ID |
| `env` | 仓储环境及 `source_card.environment` |
| `actor_kind` | `type`，保留 `bot / human` |
| `visibility` | `availability`，保留 `private / protected / public` |
| `bot_info.name` | `name`；未提供该键时使用 CSV `name` |
| `bot_info.summary` | `description`，缺失 / null 保留为 null |
| `bot_info.skills` | 独立 `name / description` 对象列表，不拼入 description、不推导 expert |
| `bot_info.domains` | 原始字符串列表，不推导能力 |
| `status` | 仅新 Card 的初始运行状态，缺失时为 offline；已有 Card 不被 CSV status 覆盖 |
| `is_deleted=1` | 删除已有 Card；不存在时保留归档删除依据，不创建有效 Card |
| `agent_code / provider_id / provider_bot_ref / created_by / connection_mode` | 非空时原样保留 |

`bot_info` 最多解码两次，解码后必须是对象。异常编码、类型、技能和缺失身份会计入 `invalid`。
已有 tombstone 不被活记录复活，计入 `skipped_tombstone`。删除不依赖旧 `bot_info` 能否解析。
源 CSV 应为每个 `(env, bot_uuid)` 一条记录的确定性快照；工具不代替权威存储选择当前 Profile。

已有 Card 的 `runtime_state / trust_level / lifecycle_state / profile_id` 在没有补充值时保持原值。
新 Card 默认 `guarded / active / default`。`profile_id` 只是单一历史兼容标识，不创建多 Profile 或激活关系。
新运行状态取 CSV `status`，不能据此推断部署系统当前在线状态。

## 可选权威资料 JSONL

每行一份明确选定的当前资料。完整环境和 Worker ID 共同确定身份；重复身份直接报错。
查找索引使用临时磁盘 SQLite，CSV 和 JSONL 逐行读取，不把整个来源表加载进内存。

```json
{"environment":"pre","worker_id":"bot:owner:worker","profile_id":"legacy:current","runtime_state":"offline","trust_level":"guarded","lifecycle_state":"active","config":{"fusion_enable":true},"archive":{"profiles":[{"profile_id":"historical","description":"历史资料"}],"md":"历史 MD","original_extensions":{"description":"历史补充原文"}}}
```

除 `environment / worker_id` 外，各字段均可省略；缺失表示不覆盖，不用 null 表示缺失。
`profile_id` 必须是一个非空字符串。多 Profile 必须先由权威存储明确选出当前资料；不能传数组，
不能按时间猜测“最新一条”。其他 Profile、旧画像、MD 和额外原文放在 `archive` 中，不能进入当前检索正文。
`archive` 是受控的本地历史资料对象，不应包含凭据；CSV 的 `session_token / bot_info.agent_token` 从不复制到 Card 或报告。
历史会话和融合记录继续保留在其权威存储中，此工具不读取、清理或覆盖它们。

`config` 必须是对象，通过 `save_config` 单独保存，不塞进 Card 正文。它与已有配置按键合并，
未给出的键保持不变；完全未提供 config 时不写配置。相同配置不会反复增加 revision。
未知补充字段、无效类型及非 JSON 数值会报错，避免把拼错的配置静默丢弃。

## 结果、重跑与注入仓储

标准输出只含 `dry_run / scope / counts / reasons`，不含 Worker 原文、凭据或原始 JSON。
`total` 与 `migrated + deleted + skipped_environment + skipped_tombstone + invalid + failed` 对账。
dry-run 的 `migrated / deleted` 是预计去向，不表示已写入。无效行和输入 / 写失败使 CLI 返回非零。
写失败立即终止，保留之前已提交的结果；不得把部分迁移解释成成功。

归档每个已处理输入行的行号、完整身份、去向、规范投影及原始行 SHA-256，并保留显式提供的历史资料。
SHA-256 按排序键、UTF-8、`ensure_ascii=False` 的行对象 JSON 计算；原始 CSV 和补充 JSONL 保持只读保存供核对。
归档不复制 CSV 中的所有扩展字段或凭据。删除记录的归档是不存在 Card 时的删除依据；须与原存储一起保留。
重跑使用新归档路径；相同 Card / 配置在公共 catalog 中幂等保存。归档失败可能发生在 Card 已提交之后，
修复归档位置后重跑即可，不回滚已保存资料。

部署迁移调用同一个函数，仓储在 OCB 组装入口注入实际公共 `SqlCatalogRepository`：

```python
from app.tc.migration import migrate_rows

report = migrate_rows(
    csv_rows, repository,
    environment="pre", legacy_lookup=lookup_authoritative_record,
    archive=write_local_audit_record, dry_run=False,
)
```

仓储 `environment` 必须与选定环境一致。`legacy_lookup(environment, worker_id)` 返回上述补充对象，
但不再包含 `environment / worker_id`；未找到时返回 None。`archive` 接收一个可 JSON 序列化的记录。
`MigrationWriteError.report` 提供安全的部分统计，不暴露底层数据库异常内容。

工具写 Card 不代表 Doc 已发布或索引就绪。后续隔离重建、冻结全部旧写入口、确定性重读追平、
配置 / 删除对账、应用重启及多实例 readiness 验证按批准的
[迁移方案](../specs/2026-11-04-tc-bcsfuse-card-migration/spec.md)执行。
恢复旧应用前应先停写并反向补齐新系统接收的资料、配置和删除，不能仅回滚镜像。
