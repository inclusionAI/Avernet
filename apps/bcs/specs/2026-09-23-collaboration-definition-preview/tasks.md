---
status: implemented
date: 2026-09-23
updated: 2026-10-27
owners: [bcs]
---

# BCS 自定义协作定义预览 Tasks

本文按 [当前 spec](./spec.md) 收敛原 T01～T05。勾选表示代码或文档已落地；历史测试结果不代表本次重新运行，真实模型、部署和私聊证据单列。新原文交付链路由 [Draft tasks](../2026-09-24-collaboration-definition-draft/tasks.md) 接续。

## T01 — 内联快照兼容

- [x] T01.1～T01.4 保留 schema 1 类型、严格 decoder、serializer、共享向量与离线 `collaboration preview`，覆盖 UUID、UTF-8、原文/消息容量、无效 YAML 和错误输出。
- [x] T01.5 通用 Skill 保留已有 YAML/运行契约；Draft 按需读取参考，不再作为通用默认交付。

工具序列化可逆，不等于模型复制长 AixUI 无损。历史模型交付失败不靠增加提示词解决，后续走 schema 2。

## T02 — 卡片与宿主 Tab

- [x] T02.1～T02.3 UMD 导出、SDK 载荷解析、点击开屏和明确错误状态已实现。
- [x] T02.4 来源消息取自宿主 DOM，Tab 去重/聚焦、冲突检测和卸载关闭已实现；旧宿主无 focus 时重建目标只读 Tab。
- [x] T02.5 宿主/组件测试覆盖加载不自动开屏、来源、重复打开、内容冲突和卸载；Draft 的固定版本/乱序/宿主 base 补充在 D04。

## T03 — 只读展示与校验

- [x] T03.1 当前使用 Loop 逻辑视图，保留嵌套、轮数和退出分支；历史响应缺元数据时回退执行投影，不改变 YAML 或编译语义。
- [x] T03.2 校验请求使用宿主可信 API base，默认 `/bcnproxy`，支持 V1 命名空间和 envelope；请求序号/取消机制防止旧响应污染。
- [x] T03.3 校验中、有效、缺图、无效、空白、服务失败和重试状态已实现。
- [x] T03.4 默认“协作预览”，子 Tab“查看定义”；源码高亮、标题旁复制，复制保持原文；成功状态不占独立块。
- [x] T03.5 schema 1 serializer/组件回归保留；新消息只传短引用，完整原文字节链路由 Draft 自动化和历史现场记录覆盖。

证据入口：[Panel 定义测试](../../assets/panel/test/collaboration-definition.mjs)、[Panel 包脚本](../../assets/panel/package.json)、[Draft 验收记录](../2026-09-24-collaboration-definition-draft/verification.md)。本次文档修订不再次承诺长 YAML 经真实模型复制的完整往返。

## T04 — 助手专用内容（独立交付）

- [x] T04.1～T04.3 工作区存在 `singlebox/agents/avernet-help/` 的助手 profile、专用 Skill 和参考；稳定 Bot 身份保留。
- [x] T04.4 既有 Skill 复制/更新/清理/失败传播回归已运行，历史及本次结果见 Draft 验收记录。
- [x] T04.5 本地专用 Skill 已改为 create/update 后显式 preview 固定版本，仅交付 preview.message；通用 Skill 不再要求所有 Bot 发送卡片。
- [x] T04.6 保留 CLI 地址发现和 Bot 身份目录，不主动覆盖 localhost/全局 --url。
- [ ] 将助手 profile 与专用 Skill 作为独立变更提交、发布并刷新实际助手；本次只提交两份 BCS spec 目录和通用 Skill。

## T05 — 历史验收与接续

| 原任务 | 当前状态与去向 |
| --- | --- |
| T05.1 Runtime/Skill 发现 | 历史助手已实际读取公共 Skill/schema；普通 Bot 分发隔离由既有回归覆盖。新专用步骤刷新后仍需实测 |
| T05.2 模型交付 | 旧内联流程失败已确认；Draft 群聊原文往返已实测。新增“每次 create/update 后显式 preview”的模型行为尚未重验 |
| T05.3 字节/容量 | schema 1 自动化保留；Draft 文件/存储/下载/Panel 原文与短引用传输有独立历史证据 |
| T05.4 模块测试 | 原 Panel、CLI、Skill 测试通过记录保留；本次按文档/Skill 范围检查，不复跑 Rust/浏览器全套 |
| T05.5 发布和其他入口 | 已有 Panel release tag；不能将标签视为所有环境部署成功。私聊按用户要求暂缓，跨版本回滚未实测 |

2026-09-24 现场内联样例：2604→2602 字节，6 处缩进变化；另一次 2536→2543 字节，11 处变化。2026-10-20 回归文件为 2174→2188 字节、14 行变化。它们解释为何切换交付方案，不作为当前方案的未修复缺陷重复排期。

## 验证入口

- `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli`：离线兼容及 Draft CLI。
- `npm run verify`（`apps/bcs/assets/panel/`）：类型、构建、组件与 UMD。
- `bash singlebox/verity/test_singlebox_profile_skills.sh`：本地 profile 分发回归，不启动实际服务或模型。
- 本次文档迁移：相对链接、状态一致性、限定提交范围、`git diff --check`；不以这些检查代替 E2E。
