---
status: implementing
date: 2026-10-22
owners: [bcs, frontend]
---

# 副屏预览与通用主屏建群实施任务

依据 [spec](./spec.md)。本轮修改 `apps/bcs/` 和 `apps/frontend/`；聊天及 frontend-nextgen 见 [roadmap](./roadmap.md)。只维护现有 CreateGroupModal：自有 Bot 选择和可选 Draft 初始内容由独立参数控制，编辑内容属于本次建群表单。

## L01：已有 BCS 用户入口

- [x] 核对当前 Human、我的 Bot / candidates 用户入口；解释本地宿主 404 与直连 V1 401。
- [x] 复用 BCS 现有浏览器认证补齐原生 Human 入口；Gateway V1 独立，不在失败后降级。
- [x] 固定版 V1 GET / validate 复用同一 Draft 应用服务、分享权限与 composition root 实例。
- [x] 保留原生路由和管理权限；V1 envelope 与原生响应使用相同的原文完整性约束。
- [x] 已有 Rust / API 契约测试与接入证据见 [verification](./verification.md) 历史章节；本轮没有修改服务端代码。

## L02：通用宿主事件与浮层参数

- [x] `onOpenCreateGroup(options?)` 注入通用 UMD 宿主；无参打开普通配置，可选 `{draft}` 只接受 schema 2 四字段引用。
- [x] BCS schema 2 仅在显式点击后传 `{draft}`；无回调、schema 1、不可用原文维持只读边界。
- [x] 原 CreateGroupModal 增加独立 `selectOriginator` / `initialDraft`；不传时保留普通建群行为。
- [x] 移除 Draft 专用 hook、wrapper 和提交 helper 命名，改用 `useGroupCreation`、通用提交逻辑及 Controller。
- [x] 相同入口重开保留内存编辑/提交状态；另一引用创建新实例，不泄漏凭据到日志或 URL。

## L03：编辑与通用提交

- [x] Draft 固定版读取时核对 ID/revision/UTF-8/hash，只初始化一次现有自由编辑器；失败阻断并可重试。
- [x] 带 Draft 固定自定义协作、隐藏群类型与模板切换，载入后自动校验并展开绑定/流程图；保留重新编辑。无 Draft 仍支持原模板和三种群类型。修改使旧校验/绑定失效。
- [x] 取消重开、身份刷新及类型切换不覆盖用户修改；编辑不写 Draft、不产生新 revision。
- [x] 可信 Human 的自有 Bot 选择独立于当前聊天身份；候选按所选发起方分页查询。
- [x] 更换发起方清空成员和绑定；同视角搜索/翻页保留选择；取消旧请求、防止异步串身份。
- [x] 普通和 Human 入口复用同一个 CreateGroupParams 组装；API 层适配既有 Human DTO，自由聊天/任务协作无需 Draft。
- [x] 自定义协作提交前快照并校验当前文本，保持字节/换行，独立提交显式角色绑定；固定执行身份拒绝。
- [x] 提交前复核用户、单次 POST、成功与未知状态锁；创建成功后身份复核失败仍归为未知，不自动重试。
- [x] 创建及用户复核成功后，自动关闭浮层与副屏，以当前 Human 视角进入服务端返回的群/初始 Session；重复提交、失败、未知结果及卸载后不跳转。保留成功/未知链接，自定义协作明确提示建群即启动。
- [x] 好友/画像筛选尚未接入 Human 契约，列入 roadmap；普通入口保留原行为。

## L04：本轮验证与文档

- [x] Panel `npm run verify` 全部通过：回调、引用、只读降级、原文/完整性及预览回归。
- [x] frontend 全量 16 套件 / 100 项通过；覆盖当前文本字节、无 Draft 三种策略、身份/角色拒绝、未知结果、单次 POST 与导航。自动跳转补充创建后身份切换、卸载与仅成功返回导航结果的回归；真实新群自动进入仍待验收。
- [x] frontend `npm run ci`、`npm run build:oss` 通过。
- [x] 类型诊断与 HEAD 对照：164 项既有诊断，逐文件/错误编号/数量一致；新增文件无诊断，未宣称完整类型检查通过。
- [x] 实际历史卡片 → 副屏 → 主屏浮层：自动校验展开、隐藏类型/模板、重新编辑/校验、三个角色绑定、再编辑阻断与取消重开保留修改。
- [x] 实际普通入口仍使用当前 Bot；副屏复制原 Draft 仍为 2174 字节且无表单修改；没有点击创建。
- [x] 同步 README / spec / roadmap / 验证记录，检查变更范围、源码行数与 whitespace。

## 保留待办（用户已明确暂缓或本轮不覆盖）

- [ ] 授权真实写入验收：现场样例及修改后的版本分别确认建群、进入新群并观察首个 Run，核对当前文本/参与者/Human 可见性和无重复启动。
- [ ] 部署宿主认证、代理、V1 envelope 与真实群导航验收；不能以 local Human 代替线上结果。
- [ ] 私聊页面端到端验收。
- [ ] frontend-nextgen 通用事件与可编辑浮层接入，详见 roadmap。

本轮未创建 Bot、好友关系、群或 Run，未修改 Backend、Gateway、Runtime、frontend-nextgen 或原有 Singlebox/profile 工作区内容，未提交代码。

## 2026-10-23：Draft 入口交互修订

- [x] 带 Draft 不提供群类型选择和自由编辑/模板切换；普通入口维持原有选择。
- [x] 初始原文载入后复用同一 YAML 校验函数，成功直接展开角色绑定与逻辑流程预览，失败留在编辑器。
- [x] 实际页面验证自动展开、重新编辑无类型/模板入口、无效 YAML 阻断及修复后展开；普通入口的类型和模板保留。全量前端 98 项、lint、构建通过；类型诊断与 HEAD 同为 164 项。

- [x] 发起方改为 select/options，移除自有 Bot 搜索和平铺按钮；实际 6 个选项选择/切换及旧绑定清空验证通过，普通数量隐藏分页。前端 98 项、lint、构建通过。
