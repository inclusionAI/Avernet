---
status: in-progress
date: 2026-09-24
updated: 2026-10-27
owners: [bcs]
---

# 协作定义 Draft Tasks

依据 [当前 spec](./spec.md)，保留 D01～D06 作为追踪入口。BCS 存储、CLI、Panel 已实现；私聊、专用 Skill 独立发布及新步骤真实模型验证仍有待办。历史测试和当前文档核对分开，见 [verification](./verification.md)。

## D01 — 固定契约

- [x] D01.1 Draft/revision、Bot owner、CAS 与 BCS 分层已实现；不绑定聊天 Session，不需要 Backend/Gateway/Runtime 新服务。
- [x] D01.2 管理认证保留；固定版读取和校验公开，无 Draft 专用 header/token。原签名鉴权要求已废止，不重新引入。
- [x] D01.3 配置仅保留 `collaboration_drafts.enabled`，默认关闭，本地配置显式开启；服务未装配返回 503，不依赖签名文件。
- [x] D01.4 新卡片 schema 2 为三字段；HTTP reference 为已部署旧 CLI 保留废弃占位 readToken，Panel/新 CLI 归一化去除；schema 1 消费保留。

## D02 — 存储、固定版校验和撤销

- [x] D02.1 SQLite 036 / MySQL 035、payload_json 原文和独立 revoked 状态、删除 tombstone 与原子 CAS 已实现；保留已提交迁移。
- [x] D02.2 创建/元数据/追加/固定读取/校验/引用/撤销/删除八操作；保存允许无效 YAML，校验失败不回滚保存。
- [x] D02.3 校验复用当前 BCS validator，返回固定 ID/版本/hash/时间；不接受替代原文，不在事务内校验。
- [x] D02.4 创建重试始终核对第 1 版，更新未知只核对 n+1；并发冲突/存储失败向上传播，不自动提高基线。
- [x] D02.5 公开读取受撤销/删除控制，owner 管理隔离、重启和旧 JSON 读取有契约测试；移除旧 read_token 数据字段时不新增 migration。
- [x] D02.6 启用/关闭不改变已存内容和撤销状态；SQLite/MySQL 共用开关；遗留签名文件不读取、不删除。

入口：[core 契约测试](../../crates/services/bcs-collaboration-draft/tests/conformance_collaboration_draft.rs)、[HTTP 契约](../../crates/adapters/http/bcs-http/tests/collaboration_drafts_contract.rs)、[bootstrap 测试](../../crates/bootstrap/bcs/src/collaboration_drafts.rs)。这些是现有测试入口，本次不将代码阅读当作重新运行成功。

## D03 — CLI 原文与结果契约

- [x] D03.1 create/update/get/validate/preview/revoke/delete 七命令，复用现有地址/认证，不发送聊天消息。
- [x] D03.2 create/update 当前仍保存、校验并取引用，返回 phase/saved/validation/reference/message；不返回完整 YAML。后续拟调整输出不算已实现。
- [x] D03.3 ID/版本/hash/字节数/标题核对、结果未知恢复、原子不覆盖下载已实现。
- [x] D03.4 preview 显式读取固定版，不新增 revision；支持无效定义诊断，不能只看 phase=ready 判断有效。
- [x] D03.5 保留离线 schema 1 preview 和原 validate/create/run；新 CLI 丢弃 HTTP 兼容 readToken，旧 CLI 可继续使用。

入口：[CLI 测试](../../crates/tools/bcs-cli/tests/collaboration_draft.rs)、[HTTP/CLI 用户故事](../../scripts/e2e-test/collaboration_draft_story.sh)。

## D04 — Panel 和宿主

- [x] D04.1 三字段引用解码/历史字段归一化，公开固定版读取/校验，无专用 header；核对原文字节/hash，支持原生和 V1 响应。
- [x] D04.2 可信宿主 apiBaseUrl/baseUrl 贯穿卡片和预览，默认 `/bcnproxy`；Tab/请求按来源和版本隔离，重开按可用状态重新读取。
- [x] D04.3 默认“协作预览”，逻辑 Loop 图与建群一致；“查看定义”内高亮 YAML、标题旁复制；通过状态合并进标题行，卡片宽度自适应。
- [x] D04.4 schema 2 的“发起协作”交给通用宿主回调，frontend 复用主屏建群浮层；此项属于 [独立建群 spec](../2026-10-22-collaboration-draft-group-launch/spec.md)，不等于 Panel 获得 Draft 写权。

入口：[定义组件测试](../../assets/panel/test/collaboration-definition.mjs)、[Draft 组件测试](../../assets/panel/test/collaboration-draft.mjs)、[宿主回调测试](../../assets/panel/test/collaboration-launch.mjs)。frontend-nextgen 和聊天建群仍在关联 roadmap。

## D05 — 通用与专用 Skill

- [x] D05.1 本地 `avernet-collaboration-help` 已要求 create/update 后显式 preview 确认的固定版本，只交付 preview.message；忽略 create/update.message，失败不回退。
- [x] D05.2 通用 `bcs-coordination` 只保留按需 Draft 存储、版本和校验说明；移除默认卡片交付流程，通用 YAML 交付规则允许专用 Skill 接管。
- [x] D05.3 保留 CLI 地址发现、Bot 目录、schema 阅读和既有 Skill 分发，格式和分发回归已通过。
- [ ] D05.4 单独提交/发布助手 profile 与专用 Skill，并刷新实际助手；当前仍在本地工作区，不包含在本次文档/通用 Skill 提交。
- [ ] D05.5 刷新后用真实模型验证每次 create/update 都显式 preview 同版、只使用 preview.message、失败恢复和重发不保存新版本。

CLI 返回字段暂时保持；将来是否让 create/update 停止生成卡片仅列 [roadmap](./roadmap.md)，不提前宣称完成。

## D06 — 验收、兼容与发布

- [x] D06.1 历史群聊：2174 字节原文经模型调用工具、正常短引用回复、历史重开、Panel 复制与 CLI 下载一致；追加、重发和撤销有记录。
- [ ] D06.1 私聊：2026-10-21 用户明确暂缓，至今未用新证据补齐；不检查或补装 bcsPanel，不以群聊代替。
- [x] D06.2 现场回归样例及容量、特殊字符、原文完整性、CAS、撤销和旧 CLI/schema 回归已接入自动化。当前最大三字段引用为 176 字节，旧 234 字节实测仅作历史记录。
- [x] D06.3 原实现的定向测试、BCS E2E、未通过的全模块/架构检查均保留历史结果；本次执行文档/Skill 检查，不重跑无生产代码改动的全套门禁。
- [x] D06.4 旧数据/同构件恢复与迁移编号例外有历史证据；当前已删除密钥依赖，不能沿用旧签名恢复要求。
- [x] D06.5 仓库已有 bcs-panel-v1.7.0 / v1.7.1 标签，当前 Panel 为 1.7.1；不再写“尚未有任何版本发布”。标签不证明部署完成，本次不发新版本。
- [ ] D06.6 新显式 preview 工作流的真实助手回归、各目标宿主部署核验，以及跨版本二进制回滚仍需独立证据。

## 本次提交边界与验证

- 两份 spec 目录移动到 `apps/bcs/specs/`，修订 spec/tasks/roadmap，历史验收单独保留。
- 与 `apps/bcs/crates/tools/bcs-cli/bcs-coordination/` 的三份 Markdown 改动一并提交。
- 助手 profile、Singlebox、本地配置和已存在的建群 spec/tasks 修改不夹带。
- 校验相对链接、当前 CLI/HTTP/schema/配置/UI 一致性、Skill frontmatter、同步后文件一致性和 `git diff --check`。
- 需要重新验证实现时，执行 CLI/Core/HTTP 相关 Cargo 测试、Panel `npm run verify` 和统一 Singlebox E2E；本次文档结果不替代这些运行证据。
