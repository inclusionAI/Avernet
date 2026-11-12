# TC 同实例读写切换与数据迁移

## 同一服务内的接口绑定

TC 在同一个服务地址提供 V1 和 V2。V1 的查询、推荐、Fuse、资料和状态更新与 V2 共用
Card/Doc 后端及索引更新任务。V1 继续使用原路径和返回格式。融合历史沿用原存储。

旧后端源码归档在 [archive/legacy-tc](../archive/legacy-tc/)。
迁移期的双后端方案见 [原迁移设计](../../specs/2026-11-04-tc-bcsfuse-card-migration/spec.md)。

## 新接口与正式导入入口

复用现有公共 `/api/v2/agent-cards/{id}` 的 PUT/GET/DELETE 和 index-status，显式绑定 TC 新服务。
单卡重建使用 `POST /api/v2/agent-cards/{id}/rebuild`，请求体为 `{"index_key":"<当前 Card 的 index-status 返回值>"}`。
接口发布该 Card 的新 Doc，Qdrant 由现有索引同步更新。Card 资料和版本保持原值。
TC 与支付宝共用 Qdrant Server 原生 BM25：名称和正文使用 multilingual 分词，名称三字符片段由 Whoosh 标准分词器生成，单独使用 BM25 评分，取最佳字段分数，不追加自定义权重。TC 不再追加名称前缀或查询侧同义词；Card 的 `domains` 仍作为明确提供的资料参与检索。关键词和向量继续并行召回，再走已有 RRF/可选精排。Python Local 不支持这套分词，明确报错；本地开发使用 Qdrant Server >=1.20。
关键词与向量继续并行召回，RRF 合并后按配置精排。名称与片段信息不修改 embedding 或 rerank 输入。
已有 Doc 缺少独立 `name` 字段，需要用已有重建能力重新生成 Doc 并等待本地索引应用。可以沿用当前 `index_key`，但仅重启或重放旧 Doc 不会补齐名称；只有重建过的 Card 获得完整的新能力。不要把局部重建当作全量生效。
`/api/v2/search`、`/api/v2/recommend` 固定查询 TC 新索引。
TC 仅补 GET/PUT `/api/v2/workers/{id}/config`，沿用已有配置请求/响应并固定保存新配置。
状态和 availability 通过新 Card 字段更新，不复制整套 v2 sync/online/Fuse 路由。

正式迁移脚本读取权威旧资料，构造规范 Card 后调用新 API。
Card 返回 202 只表示源资料提交，须继续核对 index-status、新查询和完整计数。
现有 `migrate_tc_cards.py --url` 复用相同字段转换，经 v2 串行导入并等待当前版本索引完成。
启动时从 `/ready.environment` 核对目标环境；请求或加工失败即停止并返回脱敏的部分结果。
导入历史配置时，工具使用目标 Card 当前版本写配置。

## 部署和验收

1. 配套发布 Avernet 公共代码和 OCB 内部宿主，核对 TC Card/Doc 表权限和现有索引代次。
2. 检查 `/ready` 的环境、`v1_storage=card`、`card_ready=true` 和 `index_key`。
3. 回归 V1 search、recommend、Fuse 和写入，通过 V2 回查写入的 Card 和索引状态。

如果需要恢复旧表后端，先停写并把 Card 侧新增和变更反向补齐到旧表及旧索引，再用支持旧后端的版本部署。

## 导入工具

工具读取用户提供的 `bcs_bots` CSV，默认只读盘点；显式 `--sqlite` 写隔离本地库，显式 `--url` 调用同一 TC 服务的新 API。
两种目标互斥。SQLite 模式不调用模型或构建索引；API 模式由服务生成 Doc/索引，逐条等待成功。
工具不会修改后端选择或重启服务。
CSV 只有基础信息，不能据此声称配置、历史画像、会话和所有运行状态已完成全量迁移。

从仓库根目录运行：

```bash
python apps/bcsfuse/scripts/migrate_tc_cards.py /path/to/bcs_bots.csv --environment pre

# BCSFUSE_AUTH_TOKEN 通过环境安全注入，不写入命令参数或归档
python apps/bcsfuse/scripts/migrate_tc_cards.py /path/to/bcs_bots.csv \
  --environment pre --url "$TC_BASE_URL" \
  --legacy-jsonl /path/to/selected-legacy.jsonl \
  --archive /path/to/api-migration-audit.jsonl

python apps/bcsfuse/scripts/migrate_tc_cards.py /path/to/bcs_bots.csv \
  --environment pre --sqlite /path/to/isolated-tc.sqlite \
  --legacy-jsonl /path/to/selected-legacy.jsonl \
  --archive /path/to/migration-audit.jsonl
```

`--environment` 必须明确；其他环境计入 `skipped_environment`，不修改该环境的库。
CLI 和 TC 服务统一使用 `bcsfuse_agent_cards` 与 `search_index_documents`。表名与 zbcsfuse 相同，
TC 继续使用原来的 Worker Registry 数据源，跨业务数据通过独立数据源隔离，不改接 zbcsfuse 数据源。
SQLite 演练使用单独的 TC 文件；不要将 TC 与 zbcsfuse 指向同一个本地文件。
同一 TC 数据源内的 pre/prod 仍由公共 catalog 按 environment 隔离；Qdrant 保留 TC 环境和索引代次空间。
`--archive` 使用新文件且权限为 `0600`，已有文件会报错，避免覆盖证据。
SQLite 新文件权限为 `0600`。原始 CSV、补充 JSONL、归档和本地数据库均不得提交到仓库。

## 字段与单资料选择

| 输入 | Card / 处理 |
| --- | --- |
| `bot_uuid` | 完整 `agent_id`，不拆冒号、不重造 ID |
| `env` | 仓储环境及 `source_card.environment` |
| `actor_kind` | `type`，保留 `bot / human` |
| `visibility` | `labels.visibility` 与旧兼容字段 `availability`，保留 `private / protected / public` |
| `user_visibility` | 有原值时写入 `labels.user_visibility`；缺失时保留已有标签，不从 online/offline 推导 |
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

## V2 标签与旧接口兼容

部署支持标签的版本前，在 TC 原数据源的 `bcsfuse_agent_cards` 增加 `labels JSON NULL`；
不切换到 zbcsfuse 数据源，Doc 表继续使用已有的 `filters` 列。

- V1 sync/availability 将明确提交的 `availability` 与 `labels.visibility`
  在同一次 Card 写入中保存。稀疏 sync、不涉及可见性的更新保留已有标签。
- V2 修改 `labels.visibility` 后，旧单查、批查、候选过滤和 Fuse 的 Worker 读取使用同一可见性。
  原生 Card 无需补入 `availability` 字段；已导入的 Worker Card 若含此字段，则一并更新。
  同次请求明确提交两个冲突值时返回 400，不写入。
- `user_visibility` 独立保留原始三态。V1 online/offline 仅更新运行状态，既不生成也不覆盖
  用户标签；V2 标签更新也不把用户可见性当作连接在线状态。V1 原在线过滤保持，V2 仅按显式条件筛选。
- TC 构建器版本升级为 `tc-description-v2`，使已有 Doc 的兼容可见性过滤也能正确重建。
  部署后新 TC 索引代次由现有同步链路生成，验收前等待新 Card/Doc 索引就绪；无须重新导入 Card。

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

正式部署迁移以本页前半部分的新 API 为写入入口。复用离线工具的字段归一化、
权威资料补齐和安全审计格式；不要把本地 SQLite 写入能力当作现成的线上接口迁移能力。
Doc 和索引由服务在新链路中加工，脚本核对发布状态后再执行最终追平。
回退须覆盖新业务接收的资料、配置、状态和删除，不能仅回滚镜像或配置。
