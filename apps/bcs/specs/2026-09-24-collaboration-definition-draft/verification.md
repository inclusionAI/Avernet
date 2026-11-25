# Draft 实现核对与验收记录

## 2026-10-27：当前实现和证据边界

本次从原 `docs/specs/` 迁到 BCS 并修订文档，代码核对基线为 `80fccedb63`，包含 `a6d4ff2242` 的移除读取凭据变更。下方 2026-10-20～21 原始记录保留失败、测试数量和现场 hash；**其四字段/readToken/header/密钥/默认启用要求及“未发布”状态均已过时，不是当前配置或兼容操作指南。** 当前行为以 [spec](./spec.md) 为准。

### 当前代码确认

| 核对项 | 现有实现 |
| --- | --- |
| 固定版读取/校验 | 公开，ID + revision 足够，不需要 Draft header 或读取 token |
| 管理操作 | 既有 Bot owner 认证保留；公开读取不授予写入或建群权限 |
| 引用 | 新 CLI/Panel 三字段；原生 HTTP reference 额外保留旧 CLI 所需的固定 readToken 占位，不落库、不鉴权 |
| 数据库 | SQLite 036 / MySQL 035 不变，旧 JSON 中 read_token 可忽略，新记录不再写入；revoked 仍控制读取 |
| 配置 | 仅 enabled 开关，默认关闭，本地配置显式开启；无签名材料和文件依赖 |
| CLI | create/update 仍生成 message；专用 Skill 忽略它，显式 preview 同版并只交付 preview.message |
| Panel | 宿主可信 API base、无专用 header、逻辑 Loop、高亮源码、源码 Tab 内复制、标题行显示通过状态 |
| 后续建群 | schema 2 通用回调打开 frontend 主屏浮层；本地编辑不保存 Draft，详见独立建群 spec |
| 版本 | package.json 为 1.7.1，本地 Git 已有 bcs-panel-v1.7.0 / v1.7.1 标签；本次不核验包仓库或各环境部署 |

当前 [最大引用 fixture](../../tests/fixtures/collaboration-definition-draft/maximum-reference.json) 为 **176 字节**。下面 234 字节实测含旧 readToken，仅证明当时的消息传输，不证明本次三字段工作流已完成真实模型复验。

### 本次验证范围

仅修订两份 BCS spec 目录及通用 Skill，并同步通用 Skill 到 aiworkbench；不改 CLI、Panel、HTTP 或配置源码，不提交专用助手/Singlebox 的本地改动。

- 专用与通用 Skill 的格式校验、`bash singlebox/verity/test_singlebox_profile_skills.sh` 在本次会话已通过，使用隔离临时目录，不启动真实模型。
- 文档引用/相对路径、CLI/HTTP/存储/配置/UI 一致性、限定提交范围和空白检查在本次迁移时核对。
- 当前新测试入口包括 [原生 HTTP 无凭据兼容测试](../../crates/adapters/http/bcs-http/tests/collaboration_drafts_contract.rs)、[V1 路由测试](../../crates/adapters/http/bcs-api-http/tests/collaboration_draft_routes.rs)、[bootstrap 开关/重启测试](../../crates/bootstrap/bcs/src/collaboration_drafts.rs)、[CLI 测试](../../crates/tools/bcs-cli/tests/collaboration_draft.rs) 和 [Panel Draft 测试](../../assets/panel/test/collaboration-draft.mjs)；本次未重新执行 Rust、Panel 全套或真实服务 E2E，不把阅读测试文件计为通过。
- 助手新增显式 preview 步骤尚待独立提交/刷新后进行真实模型回归；私聊继续按用户要求暂缓。跨版本二进制回滚没有补充实测。

---

## 历史记录：2026-10-20～21 Draft 实现与群聊验收

历史日期：2026-10-20～21；当时分支：`quechao_dev_1020`。当时尚未推送/发布，以下按验收发生时的版本记录，不代表当前发布状态。

### 当时实现边界（已被当前契约取代）

实现位于 BCS service-api、application、core、store、bootstrap、HTTP、CLI 和 BCS Panel；帮助工作流更新在已有帮助 profile 与 bcs-coordination 中。没有修改 Backend、Gateway、Engine Runtime 或 frontend，也没有新增资源绑定检查。沿用用户已绑定的 bcsPanel。

CLI 直接读取 UTF-8 文件，保存不可变 revision，核对固定版本后调用 BCS 校验器，返回四字段 schema 2 卡片。消息由助手正常回复。BCS 持久化原文、hash、字节数、owner、CAS head 和单版撤销状态；readToken 仅允许固定版读取和校验。保留已交付 schema 1 的只读消费者。

新增 SQLite 036 / MySQL 035；2026-10-21 rebase 时按用户明确批准的例外顺延 Draft 编号，保留主线 migration，兼容处理与本地备份见文末。Draft 默认装配；SQLite 的签名材料首次原子初始化到私有持久文件，重启复用，已有 Draft 却缺失密钥时启动失败。可选 `collaboration_drafts` 只覆盖共享部署的密钥配置。模块配置、部署与轮换说明见 [core CONTEXT](../../crates/services/bcs-collaboration-draft/CONTEXT.md)，HTTP 契约见 [OpenAPI](../../api-contracts/collaboration-drafts/v1.yaml)。

## 真实群聊链路

使用[用户提供的群聊](http://localhost:8000/bcn/chat/detail?id=bcs_grp_45ec83b2340643f2bfc7b81b6d232c5b)，原 Session 为 `bcs_grp_45ec83b2340643f2bfc7b81b6d232c5b:e9e72635`。沿用同一助手身份，通过原有 profile 文件复制函数刷新帮助 Skill。

1. 将[现场样例](../../tests/fixtures/collaboration-definition-draft/indentation-regression.json)中原本通过校验的 YAML 放入助手工作区。真实模型重新读取 Skill，调用 `draft create`，正常回复 schema 2 卡片及说明文字。没有调用 CLI 发群消息。
2. 创建 `b10d6bd5-b45d-4b6c-b564-31dd45388bd4:1`，保存与校验均成功。实际页面打开后显示原文、校验通过、3 个角色、7 个节点及 hierarchical Loop 图。
3. 在真实 Panel 点击“复制源码”，从浏览器剪贴板读取结果，与现场文件核对；助手另用 `draft get --revision 1 --output` 下载，逐字节比对通过。
4. 真实模型仅追加测试注释，使用 `expected-revision 1` 保存第 2 版，正常回复第 2 版和重新取得的第 1 版卡片。两版可分别打开，重发没有新建 Draft，第 1 版 readToken 保持相同。
5. 由助手对测试第 2 版执行两次 `draft revoke`，均成功。重启 BCS 后刷新历史消息，第 2 版显示“引用不可用”；第 1 版仍显示并复制原来的 2174 字节。

| 链路结果 | 字节数 | SHA-256 |
| --- | ---: | --- |
| 现场原文件 / BCS revision 1 / CLI 下载 / Panel 复制 / 重启后复制 | 2174 | `761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7` |
| revision 2，增加测试注释 | 2199 | `d8926902ee1b5c85fa3ee081c3f60877b29e39a7be186feba76507c95e2047ba` |
| 旧模型转抄的损坏样例，仅用于回归测试 | 2188 | 原文重新校验产生 `YAML_PARSE` |

现场部署曾复制到错误的静态文件目录；核对 BCS 配置实际指向的 `assets/panel/dist/index.umd.js` 后更新正确文件并刷新，schema 2 生效。没有改动宿主资源注册。实际联调还修正了 BCS 卡片被同一消息宽表格撑宽的问题，并使预览 grid 在窄侧栏内收缩。

第 1 版保留供复核；第 2 版分享已撤销。没有删除 Draft、创建协作群或执行流程。该样例的 judge / 提前退出业务语义未改动。

## 2026-10-20 自动化与运行记录

本轮测试入口：

```bash
cargo test --manifest-path apps/bcs/Cargo.toml \
  -p bcs-cli -p bcs-http -p bcs-collaboration-store \
  -p bcs-app-collaboration-draft -p bcs-collaboration-draft -- --test-threads=1
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --lib collaboration_drafts::tests
# 隔离 MySQL 8.4：先应用全部 001～034 migration，再提供测试 DSN
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-collaboration-draft \
  --test conformance_draft_mysql -- --ignored
cargo build --manifest-path apps/bcs/Cargo.toml -p bcs -p bcs-cli --bins
# 在 apps/bcs/assets/panel 中
npm run verify
bash singlebox/verity/test_singlebox_profile_skills.sh
bash apps/bcs/scripts/ci/arch-check.sh
git diff --check
```

| 检查 | 结果 |
| --- | --- |
| 5 个受影响 crate，串行完整回归 | 943 passed，0 failed，4 ignored；忽略项中的新 MySQL 用例已另行执行 |
| 最后补充的 CLI 集成 / 超时注入 | 8 passed / 1 passed；真实超时、截断响应、非法 UTF-8 和目标写入失败覆盖 |
| 最后补充的并发创建/core 回归 | 5 passed |
| Bootstrap Draft 配置 | 1 passed；架构 CFG-1 另运行 138 项配置测试通过 |
| 隔离 MySQL 8.4 | 全量 001～034 migration 成功，Draft conformance 1 passed；临时容器已清理 |
| BCS / CLI 二进制构建 | 通过 |
| Panel `npm run verify` | 类型、构建、UMD、focused/integration、visual、public/asset scan 全部通过 |
| 帮助 Skill 回归 | 通过；其中错误日志来自预期的失败注入用例 |
| `git diff --check` / 修改边界检查 | 通过 |
| 架构入口 | 未通过：7 passed、3 skipped、5 failed，详见下文 |

覆盖要点：

- SQLite 与 MySQL 的真实存储、CAS 原子性、创建重试、并发竞争、故障回滚、删除 tombstone；64 KiB、120 Unicode 码点、CRLF、中文、emoji、控制字符。
- owner 隔离、凭据优先且不可降级、跨部署/版本拒绝、撤销幂等、迟到创建不能重开分享、持久化重启和保留旧密钥轮换。
- HTTP 八个操作、固定版校验不接受替代原文、错误脱敏、no-store；公开 OpenAPI 引用与结构。
- CLI 原文直接上传、固定版 hash 校验、结果未知只读 n+1、畸形保存响应恢复、原子下载且不覆盖、已保存但校验失败不发成功卡片、旧命令回归。
- Panel 四字段严格解析、hash/字节数/Unicode 校验、撤销和网络错误、已核对原文在校验失败时保留、凭据变化关闭旧 Tab、来源/版本切换和迟到响应、卸载取消、旧 schema 1 和流程图回归。
- Skill 复制/更新/清理/失败传播与普通 Bot 隔离；实际助手使用新 Skill 投递短引用。

并发运行完整 CLI 测试时，两个既有测试 `cross_host_put_omits_authorization` 和 `delete_session_file_tolerates_204_no_content` 卡住；终止本次测试进程后，以 `--test-threads=1` 重跑，不修改或跳过这些测试。新增迁移导致旧迁移总数断言失败，已同步 SQLite 35 / MySQL 34，并核对新增表。

架构入口失败为既有 DEP-1/6 依赖、LINT-1 跨层导入、LINT-4 trait 命名以及 TEST-1/R25 的旧 conformance 缺项；报告没有指向新增 Draft 契约或实现。TEST-1 已完整执行 `check-r25-conformance.sh`，其中全仓测试发现也返回失败；本次 Draft 的 centralized harness 已通过实际 Cargo 执行验证。入口随后会再次调用同一个 R25 脚本，该重复执行被停止并保留失败结果，未修改检查脚本或 baseline。PR-1 因非 PR 环境、API-1 因未安装 cargo-public-api、BASELINE 因缺少 `origin/refactor_arch_bcs` 而跳过。因此本轮不能宣称架构门禁全绿。

## 验收映射与待验证范围

| 条件 | 证据 / 状态 |
| --- | --- |
| D-AC-01～09 | 存储/HTTP/CLI/Panel 合约及现场原文往返；真实版本追加、重发和撤销 |
| D-AC-10 | **按用户要求暂不验收（2026-10-21）**；实现不依赖聊天 Session，不以群聊代替私聊证据 |
| D-AC-11 | **通过**上述真实群聊、正常回复、刷新及重启后历史重开 |
| D-AC-12～17 | 严格引用、容量边界、旧 schema/CLI 回归、UI 状态和原文校验测试 |
| D-AC-18～20 | 工作流与身份保持、现有地址/认证复用、profile 回归；私聊实际投递按用户要求暂缓 |
| D-AC-21 | 现场样例、群聊、234 字节最大规范引用的正常投递/历史刷新已通过；私聊暂缓 |
| D-AC-22 | 契约、重启、撤销、密钥轮换及隔离恢复有证据；暂无上一份支持 Draft 的已发布构件，跨版本二进制回滚未实测，全量门禁单列结果 |

10 月 20 日未运行完整 Singlebox 门禁；10 月 21 日补跑结果见下文，未将未提交实现视为已发布。收尾核对 51 个新增/修改源码文件，新增源码均低于 1000 行。已有超长文件仅做局部接入：config.rs 3439→3444、server.rs 7070→7071、store lib.rs 5025→5026、CLI client.rs 4954→4957、CLI main.rs 7966→7978；未拆分文件或放宽行数门禁。

本地运行配置和签名密钥位于 ignored 的 standalone 目录，不进入版本库。发布需先部署 BCS 与迁移、保留持久密钥，再更新 CLI / Panel，最后启用帮助 Skill。回滚只能使用支持 Draft 的版本并保留数据和历史密钥；回滚到不含 Draft API 的旧二进制不能继续读取新卡片。

## 2026-10-20 历史：默认启用、配置重建与卡片宽度回归

现场曾出现 `503 DRAFT_SERVICE_UNAVAILABLE`：Singlebox 从模板重新生成两个 BCS 配置文件时覆盖了手工添加的 Draft 小节，BCS 健康但没有装配 Draft 服务。最初在 Singlebox 补过持久配置片段，并验证正常重启恢复；随后按用户要求收敛为 **BCS 默认提供 Draft**，该 Singlebox 专用函数和对应专用测试已移除，不再作为最终方案。

当时最终行为（已被当前开关及无密钥契约取代）：

- BCS 持久化启动入口始终装配 Draft。SQLite 的签名材料存放在数据库旁的 `*.draft-keys.json` 私有文件中，首次原子初始化随机部署标识及 256 bit 密钥，后续重启复用；并发初始化不覆盖已有文件。
- `collaboration_drafts` 仅作为共享部署/历史密钥的显式覆盖。非 SQLite 部署要求提供共享密钥；配置、持久化、权限或密钥错误向上传播并阻止启动。
- 默认密钥文件缺失时，store 只做一次 `SELECT draft_id ... LIMIT 1`（最多一行、包括删除标记）；发现已有 Draft 就要求恢复原签名材料，不生成新身份。正常有密钥启动和请求热路径不增加这次数据库查询。
- 当前部署原有的 `singlebox-local`、`local-1` 及密钥字节已原样保存到 BCS 数据目录；这是本次现场部署迁移，不增加临时配置发现或旧目录兼容代码。备份、迁移和回滚需保留数据库及所有签名材料。

实际两次执行 `bash singlebox/singlebox.sh --standalone restart bcs`，正常重新生成两个配置文件；确认均没有 `collaboration_drafts` 小节，服务仍正常启动，签名文件字节不变。刷新原群聊，第 1 版重新校验通过；Panel 复制仍为 2174 字节，SHA-256 为 `761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7`。

卡片宽度问题来自前一轮将 BCS 内层 article 固定为 280px，而宿主外层卡片占满一行。已将 article 改为 `width: 100%`，保留 `max-width: 100%` / `min-width: 0`。实际第 1 版卡片在宽聊天区为 586px、按钮为 558px；副屏打开后为约 239px、按钮约 211px，差值都是两侧各 14px 内边距。旧 schema 1 和新 schema 2 共用该修复。

本次补充验证：

| 检查 | 结果 |
| --- | --- |
| Bootstrap Draft 测试 | 4 passed：默认启用、重启原引用、并发初始化、共享配置、坏/缺失/不安全密钥及数据库检查失败 |
| Draft core/application/store 回归 | 134 passed，2 ignored；MySQL 等忽略用例本次未重复执行，前轮独立 MySQL 证据仍保留 |
| Panel `npm run verify` | 全部通过 |
| BCS 二进制构建 | 通过 |
| Singlebox 正常启动入口 / 群聊历史预览 | 无 Draft 配置块，原签名材料及 2174 字节原文保持一致 |
| `git diff --check` | 通过 |

没有修改 Backend、Gateway、Engine Runtime 或 frontend。新增/修改的 Draft 源码均低于 1000 行；既有超长 config.rs/server.rs 保持局部接入，未拆分或放宽检查。单独在地址栏打开 revision API 不携带卡片的 `X-BCS-Draft-Read-Token`，返回 401 属于预期认证行为；从卡片打开时由 Panel 在请求头携带凭据。

## 2026-10-21 收尾验证

用户明确“私聊页面先不用验收”，因此本次不探测资源绑定、不要求私聊入口，也不将 D-AC-10 勾为通过。源码、命令、配置文档和 tasks 已同步默认启用、卡片宽度及副屏交互；编辑、review、群 apply 仍属于 roadmap。

### 副屏与规范引用

- 副屏默认“预览”，源码放在独立子 Tab，顶部“复制源码”在两个视图均可用；未取得可信原文时禁用。自动化覆盖键盘切换、原文字节不变、切换视图不重复请求、切换版本恢复预览、引用不可用和迟到响应。
- 新增 [maximum-reference.json](../../tests/fixtures/collaboration-definition-draft/maximum-reference.json)：规范 UUID、最大安全整数 `9007199254740991`、32 字节公开测试值的规范 base64url，共 **234 UTF-8 字节**。CLI serializer 测试逐字节比较同一 fixture，1 passed。
- 真实助手从工作区文件读取该样例，通过当前群聊正常回复；持久消息 `31267dac-4258-4220-967f-338e6a535f64` 中的 AixUI 解码后与 fixture 完全一致。浏览器刷新后仍显示该固定版本，点击进入读取流程并显示“引用不可用”，复制源码禁用。该样例是明确标注的虚构资源，验证的是最大格式/长度传输及严格解码；它不证明最大 revision 存在，也不代替真实现场 Draft 的原文往返证据。
- 恢复原群聊页面后再次打开第 1 版，默认预览及源码子 Tab 均能复制 2174 字节，SHA-256 与现场相同；已保存实际截图。Panel `npm run verify` 和帮助 profile Skill 回归再次通过。没有修改 Backend、Gateway、Runtime 或 frontend 源码。

### 真实 HTTP / CLI 用户故事

新增 [collaboration_draft_story.sh](../../scripts/e2e-test/collaboration_draft_story.sh)，接入既有 `e2e.sh`，补齐 `collaboration draft <leaf>` 的 CLI 覆盖记录；不降低既有覆盖率要求。

独立运行结果：**32 passed / 0 failed**（首次 29 项，覆盖率检查发现旧 preview 漏测后补充 3 项并重跑）。使用已有 Bot 身份上传现场 2174 字节文件，覆盖八个 HTTP 操作、七个 Draft CLI 叶命令及旧离线 preview：保存与固定版校验、无 Session 的持卡读取、no-store、共享不可写、原子下载逐字节核对、CAS 冲突、追加后旧版读取、引用重取稳定、重复撤销、删除后的迟到创建拒绝。只删除该故事新建的临时 Draft，现场历史 Draft 保留。

### 数据、密钥与构件恢复

在临时目录复制现场 Draft/revision 行及原签名文件，以独立端口启动 BCS；不复制 Bot、聊天投递或通知数据，不修改现场数据库。演练完成后销毁临时数据和密钥副本。

| 检查 | 结果 |
| --- | --- |
| 从复制的 Draft 数据与原签名文件启动 | revision 1 返回 200，2174 字节及 SHA-256 与现场一致；revision 2 返回 404 |
| 停止进程、移除副本签名文件后启动 | 明确失败；未生成替代密钥，未开放服务 |
| 恢复原签名文件再启动 | revision 1 原文不变；revision 2 仍拒绝；签名文件字节不变 |
| 演练 BCS 构件 SHA-256 | `78dc8a110c062fe9c577555a697144c3e515865263af08cdf0387cf95c16c0d5` |

这是**同构件的数据/密钥恢复演练**，不能称为跨版本回滚。演练时的 HEAD `168a7bfdd8` 尚无 Draft API，没有可用的上一份 Draft 发行构件，因此跨版本回滚仍未实测。首次发布应保存这次构件、配置、迁移状态及数据/签名文件备份；后续版本只能回退到支持 Draft 的构件，保留历史密钥及撤销状态，不回滚数据库删除已经产生的 revision。此前旧二进制无法服务新卡片的限制不作兼容伪装。

### 全量门禁

执行既有入口，使用独立 BCS/Gateway/前端端口；全模块结果为未通过，详见下表。环境重试过程：第一次 PyPI 下载超时；第二次发现调用终端继承的 `LOG_FORMAT=json` 不符合 BCSFuse 的 Python logging 格式，后续用 `env -u LOG_FORMAT` 清除；第三次隔离 Gateway 的 8890 端口与门禁 mock 冲突，改用空闲 8895。未修改这些服务的实现、测试阈值或检查脚本。

收尾再次运行 `bash apps/bcs/scripts/ci/arch-check.sh`，重现既有 DEP/LINT/R25 缺项。其 R25 全仓 `cargo test --workspace --no-fail-fast -- --list` 阶段持续超过 20 分钟，已停止本轮进程树；未取得完整成功结果，也未修改架构检查或基线。最终局部回归为 CLI 8 passed、Draft core 5 passed，MySQL conformance 重新编译通过（本轮未再次连接 MySQL）；工作区新增行使用现有凭据扫描器检查为 0 findings，`git diff --check` 通过。


完整 Singlebox 运行命令：

```bash
env -u LOG_FORMAT UV_HTTP_TIMEOUT=120 \
  BCS_PORT=21010 BCS_API_BASE_URL=http://127.0.0.1:21010 \
  FRONTEND_PORT=8001 GATEWAY_PORT=8895 \
  bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

| 全模块运行检查 | 实际结果 |
| --- | --- |
| Backend live acceptance | 59 passed、1 xfailed |
| BCS 用户故事断言 | 750 passed、0 failed；包含首次 29 项 Draft 故事 |
| BCS 行 / 方法覆盖率 | 47.23% / 40.91%，通过 41% / 36% 门槛 |
| BCS HTTP 端点覆盖 | 162 / 162，100% |
| BCS CLI 覆盖 | 67 / 68，遗漏旧 `collaboration preview`；已在 Draft 故事补上 schema 1 和原文字节兼容检查，独立 32 项通过，并重跑 BCS 门禁 |
| Backend expert_chat 模块覆盖 | core 49.00% < 61.18%；router 7/11（63.64%）< 100%；未改动该范围外模块或阈值 |
| 规范产物校验 | 返回失败：全模块 summary 与 BCS 首次 CLI 覆盖未通过；未改写原报告为成功 |
| E2E changed-line coverage | 本次未提供已提交变更基线，因此脚本跳过；不将其宣称为通过，发布时仍需按提交范围执行 |

全模块的 `summary.json`、`summary.md`、`dashboard.html` 及其 BCS 子报告保留在 `singlebox/.dependencies/coverage/singlebox/reports/`，明确记录失败。BCS 补漏后的独立结果单列，不能抹掉全模块的 expert_chat 失败或冒充全模块已通过。


### BCS 补漏后的最终结果

以最终 Rust 源码重新构建 instrumented BCS，运行完整 BCS E2E，使用同样的隔离目录/端口与 mock 环境，并显式保留门槛：

```bash
# 沿用上述隔离 BCS 数据、Bot profile 和端口环境；SINGLEBOX_MODEL_CONFIG_MODE=mock
bash apps/bcs/scripts/e2e_coverage.sh --force-rebuild \
  --bcs-line-min 41 --bcs-method-min 36
```

- **751 passed、0 failed**；含旧离线 preview 与 Draft 的 32 项回归。
- 行覆盖率 **47.11%**、方法覆盖率 **40.58%**，满足 41% / 36%。
- CLI **68 / 68（100%）**，旧 `collaboration preview` 与七个 Draft 命令均实际执行。
- 原端点清单只扫描 `router.rs`，未发现通过 `merge` 挂载的 Draft 模块。已局部修正 `adapters_endpoint_coverage.py` 以跟随显式挂载，不扫描未挂载模块；新增回归后 **4 passed**。使用同一次已完成 E2E 的真实请求日志重新执行 `--min 100`，得到 **170 / 170（100%）**，八个 Draft 操作全部列入分母及覆盖项。旧 162/162 结果不作为 Draft 路由覆盖证据。
- E2E changed-line coverage 仍未执行，待提交后的真实变更范围门禁；没有放宽阈值或创建豁免。

独立最终报告保存在 `singlebox/.dependencies/coverage/singlebox/reports/bcs-followup/`，包括原 E2E 日志、修正后的端点重算日志和测试结果。全模块原 `bcs/` 及 summary 保留失败事实，未拼接或改写成全模块成功。

测试结束后恢复 `localhost:8000` 原群聊页面与手工模型配置；协作助手原进程仍在线。再次通过页面核对第 1 版预览和两处复制得到相同 2174 字节，历史原文及第 2 版撤销状态均未改变。工作区保留此前已存在的 profile 迁移，未提交、推送或发布；私聊及跨版本回滚不计为已验收。

## 2026-10-21 Loop 逻辑视图补充验收

按用户补充要求，仅在 BCS Panel 对齐建群预览的逻辑视图：各轮执行实例归并为一份循环体，保留 Loop 容器、轮数、回边、退出分支和嵌套层级；画布默认适配副屏宽度。历史响应缺少 Loop 描述时保留其原有执行投影，不修改固定版 YAML 或校验摘要。

- `npm run verify` 通过；最终保护性错误处理后复跑 typecheck、build、定义/草稿组件测试、UMD 契约和 public/asset scan，均通过。
- 覆盖 20 轮归并、单轮无回边、break/exhausted 分支、同名嵌套 Loop、缺失元数据错误及旧响应。
- 原群聊固定版本 1 实测显示“写稿、审稿、发表”3 个逻辑节点、1 个 Loop 容器、1 条回边；源码仍为 2174 字节，SHA-256 `761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7`，预览复制与源码子 Tab 一致。

## 2026-10-21 提交修订与 rebase

修订本分支功能提交，并以本次 fetch 得到的 `origin/dev`（`6c5b709df8`）为新基线。主线新增的 group-context 命令文档、owner 迁移及相关实现均保留。BCS Panel 源码与 rebase 前一致；未另行修改 Backend、Gateway、Engine Runtime 或 frontend。原功能提交已有的六个 Backend Draft 骨架文件保持原样、未接入服务，不视为本次交付能力。

### 迁移编号例外及本地升级

主线占用了 SQLite 035 / MySQL 034。用户明确批准保留主线编号，将本分支 Draft 顺延为 SQLite 036 / MySQL 035，并在备份后校正当前本地 SQLite 历史记录、补上主线迁移。这是针对此次编号冲突的例外，不新增忽略 checksum 或自动改写历史的兼容逻辑；主线迁移文件未变，Draft DDL 内容也未变。

- 先使用 SQLite backup API 复制本地库进行演练；完整 Draft/revision 行、撤销状态及 1～34 的迁移记录保持一致。
- 正式备份位于 ignored 的 `singlebox/.dependencies/standalone/bcs_data/rebase-backup-20261021/`，包含一致性 SQLite 快照和原签名材料，文件权限为 0600、目录为 0700。备份不进入 Git。
- 正式校正在单个事务内执行：检查原 035 的 name/dialect/checksum 和当前最大版本；把 Draft 记录改为 036 并更新对应 checksum；执行主线 035 的 `owners_json` 列迁移并记录历史。事务内逐行核对全部 Draft/revision 与 1～34 历史，任一断言失败即回滚。
- 结果：4 条 Draft、3 条 revision 完全一致，签名材料不变，SQLite integrity check 为 `ok`；新 `bcs-admin db migrate --check-db` 返回 current/target 36、pending 0。现场第 1 版仍为 2174 字节及原 SHA-256，第 2 版仍撤销。
- 如需恢复此次本地操作，先停服务，再配套恢复备份数据库、签名材料及 rebase 前构件；不能只恢复旧迁移记录而继续使用新注册表。Git 已保留修订前和 rebase 前的备份分支。

### rebase 后定向验证

| 检查 | 结果 |
| --- | --- |
| SQLite migration 单元回归 / provider slug 升级 | 41 / 1 passed |
| bcs-admin migration 回归 | 63 passed，5 ignored；其中 MySQL 全链另行执行 |
| 隔离 MySQL 8.4 全量 001～035 | 新库应用、重复运行、v20 升级及历史一致性全部通过，1 passed |
| MySQL Draft 真实存储契约 | Text / Prepared 协议、事务与 UTF-8 回归，1 passed；临时容器已删除 |
| Draft core/application 契约与回归 | 6 passed；MySQL ignored 用例已如上另跑 |
| Draft HTTP / CLI 集成 | 2 / 8 passed |
| CLI 旧离线 preview 回归 | 5 passed |
| BCS / bcs-admin / bcs-cli 构建 | 通过 |
| 帮助 profile 回归 / 端点清单回归 | 通过 / 4 passed |

重新核对相对主线新增/修改的 69 个源码文件：新增文件均不超过 1000 行；已有超长 config.rs、server.rs、store lib.rs、CLI client.rs/main.rs 和 singlebox/modules/bots.sh 保持局部修改，没有拆分或放宽门禁。

此前完整 BCS E2E 和 Panel 验证记录仍保留；本次未重跑全量 Singlebox、架构和 changed-line 门禁，不能把定向验证视作这些门禁已通过。私聊继续按用户要求暂缓。提交仅在本地修订与 rebase，未推送或发布。
