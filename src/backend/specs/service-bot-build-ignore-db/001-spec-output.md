---
agent: tc-review
status: completed
created: 2026-09-20T22:55:27+08:00
iteration: 1
---

# 系分 Spec：数据库集中管理构建排除规则

## 需求概述

将跨引擎的服务 Bot 构建排除配置集中保存在 Backend 独立表。构建一次读取规则快照，在发布物生成时排除；现有容器 publish-ignore 接口和 DaaS 安装阶段规则保持原样，作为实例级补充。用户已明确直接实现、基于 GitHub dev 完成本地门禁、rebase 和 PR；不部署、不 merge。本轮不实现绝对路径，也不自动迁移容器文件。

## 编码 Spec

### 功能点

- [ ] 新增独立配置表及项目既有数据库初始化/升级入口，兼容 SQLite 与线上数据库。
- [ ] 在现有 publish router 平放 GET/POST `/api/service-bot/publish/ops/build-ignore`，不替换实例级 `/publish-ignore`。
- [ ] GET 参数 `bot_id/entity_id`；POST 同加 `operation=add|remove`、`path`。引擎取已鉴权 Bot 的当前有效引擎，响应明确 `engine_type/scope=build/paths/revision/changed`。不接受 stage/version；未来切换引擎时读取对应独立配置。
- [ ] 管理员可操作授权租户内 Bot；普通用户复用现有 publish-ignore 的 Bot 管理权限。不存在或无权访问的 Bot 在读写规则前拒绝。
- [ ] 构建读取一次数据库规则；无记录视为空，数据库异常不得回退空规则。规则变更不影响已开始构建的快照。
- [ ] 主复制、extra include、extra sync 全部使用同一快照；不破坏内置排除及共享 Skills manifest 契约。
- [ ] 成功构建在发布单 `ext.publish_ignore` 记录 `engine_type/paths/revision`。空快照也显式记录。现有发布物的安装、重启及回滚不读取当前配置。

### 既有链路与允许变更点

`BuildStageRunner → ArcaSnapshotProducer → BotBuildService.build → _migrate_bot_instance`。主 rsync 已带 `--delete --delete-excluded`；之后 `_sync_extra_include_files` 和 `extra_sync`，再生成 MCP 和 OpenClaw 阶段配置。producer 的白名单 ext 透传必须显式补快照，否则仅修改 build 返回值不会持久化。

允许新增：Backend 中性模型/API 协议、领域服务、repository/model、DI、现有 router 的两个 endpoint、构建规则应用和 producer 快照透传、迁移脚本、测试及前端接口说明。禁止修改：Engine、DaaS 脚本、原 publish-ignore、连接解析、ARCA/BaaS 客户端、通用命令执行器、普通重启/原地重启。

### 技术方案与规则契约

规则基准为 `EngineBuildPlan.migration_subpath` 对应发布物内部根目录；不是宿主机/NAS 绝对路径。`workspace/bin` 精确匹配该项及后代，不匹配 `x/workspace/bin` 或 `workspace/binary`。复用/收敛现有字面相对路径规则：可将前导 `./`、末尾 `/` 规范化；拒绝空/root、绝对路径、`.`/`..` 段、换行/NUL/控制字符及 glob 元字符。标准化后去重，幂等 add/remove；路径及数量上限沿用既有 ignore 限制，不任意放宽。

主复制新增 `--exclude=/workspace/bin` 等锚定模式，保留既有内置 excludes。DB 规则独立传递，不能把其主根模式未经转换用于额外根。所有 rsync 参数使用 argv，不拼接 shell。

额外复制契约：

1. extra include 在探测源文件及 device fallback 之前判断其发布物目标相对路径是否被排除；命中直接跳过，不能随后补回。
2. extra sync 目标前缀为 `claude` 时：规则 `claude/skills/foo` 转为 extra rsync `/skills/foo`；规则 `workspace/bin` 不用于 extra root；规则 `claude` 命中整个额外根时不复制。主 rsync 的既有 delete-excluded 保证重试时清除该被排除目标，不对运行容器新增删除操作。
3. 保护实际将生成/必需的文件：`build_plan.mcp_config_relpath`，OpenClaw `openclaw.json`（输入配置）、`openclaw_verify.json`、`openclaw_online.json`、`openclaw_eval.json`。排除该文件或其祖先拒绝，并返回具体冲突路径。只按真实生成/依赖路径保护，不扩展到所有 workspace/skills。
4. 当前 shared corpus / active Skills manifest 必须仍能通过原验证；若用户排除破坏其必需 active root，构建明确失败，不伪造 manifest 或无条件重建已排除内容。

重试/回滚：一次新的 build 调用读取当时配置并返回所用快照；同一版本失败重试可读取新配置，版本目标按既有删除语义清理。成功发布物后续 verify/online/restart/rollback 沿用产物和已保存快照，不因当前规则变更重新过滤。规则修订号使用整数递增，与旧实例文件 SHA256 revision 不混用。

### 关键方法抽象

| 方法 | 层 | 职责与输入输出 | 协作与副作用 |
|---|---|---|---|
| `BuildIgnoreService.query/change` | 应用 | 鉴权 Bot、确定引擎；查询/幂等 add/remove；返回规则快照或明确错误 | Bot/权限 repository、规则 repository；无 Engine 调用 |
| `BuildIgnoreRepository.get/change` | 持久化 | 按 env/entity/bot/engine 读取和原子修改；返回 revision/paths/changed | SQL 事务、唯一键与 CAS/锁，不能 read-modify-write 丢更新 |
| 规则归一化/目标匹配/extra 根映射 helper | 中性规则/构建 | 规范字面路径、精确子树匹配、将发布物路径映射到 extra root | 纯函数，不解析宿主文件系统或跟随符号链接 |
| `BotBuildService.build/_migrate_bot_instance` | 构建 | 捕获单次快照、保护必需配置、三条复制路径应用相同规则 | NAS 构建目标写入；保留原 rsync/错误传播 |
| `ArcaSnapshotProducer.produce` 的结果透传 | 发布应用 | 将成功构建实际快照纳入现有 artifact ext 合并 | 不重查当前规则，不覆盖其他 ext |

职责界限：router 仅参数/认证及响应转换；权限、引擎选择在 service；事务并发在 repository；源根映射由 build plan 驱动，不按 OpenClaw 硬编码。

### 关键领域模型设计

#### BuildIgnoreConfig

一个 env/entity/Bot/engine 的“下一次构建配置”，生命周期独立于运行容器和发布单。

| 字段 | 类型 | 必填/约束 | 来源及兼容性 |
|---|---|---|---|
| id/config_key | 主键/唯一有界键 | 唯一 | 沿用现有 BotStartupScriptModel 长实体标识哈希唯一键方案；长度前缀编码后哈希，不简单分隔拼接 |
| env | 字符串 | 必填 | 当前可信环境 |
| entity_id, bot_id | 字符串 | 必填 | 已鉴权 Bot；保留既有宽度 |
| engine_type | 字符串 | 必填 | 当前 Bot/provider 标准引擎名 |
| paths | JSON 字符串数组 | 必填，默认 [] | 标准化、去重后的相对规则；无记录等价 [] |
| revision | 整数 | 非负；实际变更递增 | 空查询 0；幂等操作不递增；并发冲突不得丢失其他规则 |
| modifier | 字符串 | 必填 | 认证 operator，非前端可伪造字段 |
| gmt_create/gmt_modified | 时间 | 按项目约定 | 更新只发生在实际变更 |

数据库实例/tenant 路由遵循 DatabasePlugin，唯一键至少 `(env, entity_id, bot_id, engine_type)`；若现有共享库 tenant 需要显式维度则纳入。删最后规则保留空配置行和 revision，避免 ABA 及修订号倒退。

#### PublishIgnoreSnapshot

发布产物的值对象，字段 `engine_type: str, paths: list[str], revision: int`，来源为本次构建固定副本；不得保存容器连接、凭据或运行时绝对路径。成功结果与发布单 ext 一致；旧发布单缺快照保持旧兼容语义。

### 外部系统边界日志

事件至少覆盖 `backend.build_ignore.request/response/failure`、`build_ignore.snapshot`、`build_ignore.transfer`。HTTP 事件含 system/direction/operation/method/route、关联 ID（存在时）、bot/entity/engine/operator、规范化操作与路径、结果/revision/changed、耗时；失败含安全业务错误码和异常类别。复制阶段含 publish/version（存在时）、revision、规则数量、主根/extra 阶段、退出状态与耗时。路径列表过大只在日志记录数量/摘要，不截断 API。不得记录原始 request/headers、token/Cookie/secret/session、完整 SQL 异常参数或未脱敏 subprocess 错误。新增读取无外部 Engine/BaaS 请求；沿用既有本地 rsync 日志。

### 文件改动范围

| 路径/模块 | 类型 | 说明 |
|---|---|---|
| `community/kernel`、`community/api`、`community/core/service_bot` 的 build-ignore 模型/服务 | 新增 | 中性协议与应用逻辑 |
| `community/core/repository`、数据库 models/schema/初始化、DI | 新增/修改 | 表、原子持久化及绑定 |
| 现有 service bot publish router | 修改 | 两个平放 endpoint |
| `bot_build_service.py` | 修改 | 快照及各复制路径过滤 |
| `deploy/arca_snapshot_producer.py` | 修改 | artifact ext 透传 |
| 对应单测/接口登记/前端文档 | 新增/修改 | 行为验证与调用说明 |

### 验收标准

- [ ] 修改/查询不调用 Engine；容器离线可管理构建规则；旧实例接口回归保持不变。
- [ ] 相同 Bot 不同 env/entity/engine 隔离；非管理员无 Bot 管理权限时不能读写。
- [ ] 三条复制路径无绕过；主构建重试清除前次残留；容器本地规则不受影响。
- [ ] 成功产物 ext 是实际构建快照；并发修改规则不会改变本次快照。
- [ ] 单测通过且改动文件行覆盖率 >90%，显式实测不降门禁。

## Review Spec

- [ ] 不创建 child router，不让 core 反向依赖 API adapter；DI 与数据库初始化完整。
- [ ] 原子 add/remove 及唯一键保护通过并发测试，不丢更新、不伪造 revision。
- [ ] exact subtree 语义和多源映射一致；必需配置不会排除后重新生成造成虚假承诺。
- [ ] 快照真实通过 producer 与发布单 ext 合并，不仅是 mock 返回。
- [ ] 无修改无关 Engine/DaaS/连接/重启模块；无新增 unused import/变量、孤儿代码、格式告警。
- [ ] 日志成功失败和凭据脱敏均有断言；不得打印完整敏感异常。
- [ ] 模型/关键方法职责与本 Spec 一致；覆盖率不足判 REJECT。

## QA Spec

| 编号 | 用例 | 预期 |
|---|---|---|
| TC-01 | 无记录查询、添加、重复添加、删除、不存在项删除 | 空0/增1/幂等revision不变；最后删除保留空行 |
| TC-02 | 管理员、owner/管理员协作者、普通无权用户、Bot不存在 | 对齐旧管理权限，拒绝路径不触达配置读写 |
| TC-03 | 同 Bot 跨 env/entity/engine | 完全隔离；API引擎不可客户端冒用 |
| TC-04 | Unicode/空格路径、./归一化、深层目录、同名前缀 | 正确字面匹配；workspace/bin 不影响 workspace/binary |
| TC-05 | absolute/root/../glob/控制符/超限 | 稳定错误，无 shell 注入/规则注入 |
| TC-06 | 并发 add不同路径、add/remove、首写冲突 | 不丢更新，事务/CAS有限冲突处理 |
| TC-07 | 主根实际临时目录构建 + 同版本已有残留 | 发布物排除子树；仅版本目录清理，不修改源 |
| TC-08 | extra include 命中、NAS权限 fallback | 不探测/不device补回被排除文件 |
| TC-09 | extra_sync claude/skills/x、整claude、不相关workspace规则 | 正确剥离前缀/整根跳过/不误排除extra同名目录 |
| TC-10 | MCP/阶段配置及祖先规则 | 配置冲突明确失败，生成步骤前阻断 |
| TC-11 | 构建期间变更规则、DB失败、producer ext合并 | 单次固定快照；DB失败不降级；ext包含正确快照 |
| TC-12 | 失败重试、已构建重启/回滚、旧无快照发布单 | 新build可取新快照；产物复用不重查；旧行为兼容 |
| TC-13 | shared Skills manifest、原 publish-ignore 回归 | 旧契约不被静默破坏 |
| TC-14 | 成功/失败日志及敏感测试值 | 关键字段存在，原始凭据不落日志 |

前置条件：使用 SQLite 临时库和临时源/目标目录；禁止用真实 Bot/预发数据库进行写入验收。基线先记录；真实 rsync 不可用时单独标明环境限制，不用纯字符串断言冒充实际文件排除证据。

## Ship Spec

- 本轮仅本地验证与 GitHub PR；不执行线下/预发部署。
- 开发：`feat/service-bot-build-ignore-db`；目标：GitHub inclusionAI/Avernet `dev`；基线 `7d39e392b`。
- 提交后以最新 github/dev 为底新建 rebase 结果分支，原 feature 保留。PR CI/ACI 与本地验证独立，pending 不报完成。
- 回滚：回退应用功能提交；新表属增量配置，保留数据不删表。历史发布物不改变，原实例接口/容器规则仍可使用；数据库迁移发布必须先于启用新代码。
