# TC 同实例读写切换与数据迁移

> 2026-11-05：代码及本地同实例验证已完成，未部署或迁移线上数据。部署前仍须核对实际表结构、权限、模型与数据清单。

## 同一服务内的接口绑定

同一套新部署的 TC 服务，在同一个服务地址提供旧 API 与新 `/api/v2` API。
旧接口原路径和格式保持；新接口不是另一个实例的独立地址。

| 接口 | 切换前 | 配置修改并重启后 |
| --- | --- | --- |
| 旧 search/recommend/Fuse 和资料/配置/状态读取 | 旧表及旧索引 | 新表及新索引 |
| 旧 sync/online/offline/availability/config/DELETE 等更新 | 只更新旧表及旧索引 | 只更新新表及新索引 |
| 新 API 的读取、查询和写入 | 只使用新表及新索引 | 仍只使用新表及新索引 |

TC 专属启动配置为 `BCSFUSE_TC_V1_STORAGE=legacy|card`，默认 legacy，修改后重启生效。
它一次选择旧接口整套数据、状态、配置和索引后端。新 API 不读取此配置。
不接受仅切索引的旧配置作为别名，不做混合优先读取、缺失回查、自动搬迁或双写。
card 模式两套 API 共用一套新服务和索引加工任务，不再初始化旧资料/状态/向量仓储；融合历史仍使用原权威存储。配置非法或目标失败不能自动回退。

## 新接口与正式导入入口

复用现有公共 `/api/v2/agent-cards/{id}` 的 PUT/GET/DELETE 和 index-status，显式绑定 TC 新服务。
`/api/v2/search`、`/api/v2/recommend` 固定查询 TC 新索引。
TC 仅补 GET/PUT `/api/v2/workers/{id}/config`，沿用已有配置请求/响应并固定保存新配置。
状态和 availability 通过新 Card 字段更新，不复制整套 v2 sync/online/Fuse 路由。

正式迁移脚本读取权威旧资料，构造规范 Card 后调用新 API；切换前不能用旧 sync 导入新表。
Card 返回 202 只表示源资料提交，须继续核对 index-status、新查询和完整计数。
现有 `migrate_tc_cards.py --url` 复用相同字段转换，经 v2 串行导入并等待当前版本索引完成。
启动时从 `/ready.environment` 核对目标环境；请求或加工失败即停止并返回脱敏的部分结果。
配置版本属于各自仓储，不能把旧 GET 的 version 当作新表版本重放；工具使用新表当前版本写配置。

## 部署、追平与切换

1. 核对两仓配套版本、旧表及 TC 新表结构/权限、环境与索引隔离。迁移时沿用已验证的模型/维度，
   不同时做模型换代。默认 legacy 部署同一新版本服务。
2. 用同一地址验证旧接口实际读写旧表、新接口实际读写新表，包含相同 Worker ID 的资料/配置/状态隔离。
   旧接口业务继续工作，新 API 准备新数据；旧接口成功不能拿新 Doc 就绪冒充旧写入成功。
3. 分页、幂等调用新 API 导入与核对。迁移清单内旧表仍为权威，新 API 用于同步导入，
   避免另一业务写方同时修改这些对象；新 API 独有记录不按旧表清单删除。
4. 短暂停止两套 API 的业务写入，只允许迁移脚本写新表；排空旧后台写任务。
   确定性重读、重放，核对资料、当前 Profile 标识、画像归档、配置、状态、删除和新索引。
5. 将旧接口后端配置改成 card 并重启同一服务。地址和路径不变，旧查询与旧更新一起改用新服务，
   新 API 保持原绑定。核对每个实例实际配置及旧 search/recommend/Fuse、写入冒烟后恢复业务写入。

`/ready` 需报告实际加载的旧接口后端和相关索引准备状态；不能把进程存活、空索引就绪或单次 sync
成功当作全量迁移完成。旧接口后端 card 下，撤去旧 Worker/Profile/运行状态/向量表的访问后两套 API 仍必须正常工作。
融合历史可能与旧资料处在同一物理库，不能把其访问一起撤掉。
新业务写入发生后，回退须停写并反向补齐旧表/旧索引，再改配置重启；不能只把开关拨回去。
融合历史沿用原权威存储，不因切换清空。zbcsfuse 代码、配置、表和集合不随 TC 切换改变。

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

- legacy 模式的旧接口仍只更新旧表；V2 标签始终只影响新 Card/Doc。
- card 模式的旧 sync/availability 将明确提交的 `availability` 与 `labels.visibility`
  在同一次 Card 写入中保存。稀疏 sync、不涉及可见性的更新保留已有标签。
- V2 修改 `labels.visibility` 后，旧单查、批查、候选过滤和 Fuse 的 Worker 读取使用同一可见性。
  原生 Card 无需补入 `availability` 字段；已导入的 Worker Card 若含此字段，则一并更新。
  同次请求明确提交两个冲突值时返回 400，不写入。
- `user_visibility` 独立保留原始三态。旧 online/offline 仅更新旧运行状态，既不生成也不覆盖
  用户标签；V2 标签更新也不把用户可见性当作连接在线状态。V1 原在线过滤保持，V2 仅按显式条件筛选。
- TC 构建器版本升级为 `tc-description-v2`，使已有 Doc 的兼容可见性过滤也能正确重建。
  部署后新 TC 索引代次由现有同步链路生成，验收前等待新 Card/Doc 索引就绪；无须重新导入 Card。
  legacy 旧索引以及 zbcsfuse 的索引和构建规则不变。

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

## 旧模式恢复边界

旧 SQL 的 Worker/Profile/运行状态变更共用一次事务；索引发布前校验当前 Worker 版本，
生产 SQL 使用短期行锁把版本校验与发布串行，Embedding 计算不持该锁。
原仓储格式不变，恢复标记仅在本适配写入过的当前 Profile 元数据内保存。
已有无标记历史向量启动直接沿用；缺向量、孤儿向量或受管对象版本不一致才修复旧索引，
不从旧表自动生成新 Card。SQLite 为单实例开发后端，跨实例事务与行锁须在真实 ZDAS/MySQL 验收。
