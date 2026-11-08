---
status: planned
date: 2026-09-23
updated: 2026-10-27
owners: [bcs]
---

# 协作定义预览之后的 Roadmap

当前能力见 [spec](./spec.md)，后续需求统一进入 [Draft roadmap](../2026-09-24-collaboration-definition-draft/roadmap.md)。本文件仅保留原规划的去向，避免继续按“消息内完整 YAML”建立第二套编辑和版本系统。

| 原规划 | 当前状态 / 入口 |
| --- | --- |
| 内联快照只读预览 | 已实现，保留 schema 1 历史兼容；新助手交付使用 schema 2 |
| 服务端 Draft | 已实现，固定 revision、CAS、原文下载与可撤销公开读取 |
| 用户从预览发起协作 | 已有副屏按钮 + frontend 主屏建群浮层，见 [建群 spec](../2026-10-22-collaboration-draft-group-launch/spec.md) |
| 编辑定义 | 建群浮层可编辑本地 YAML，但不写回 Draft；副屏编辑并保存 revision 仍待设计 |
| Bot review、新版本通知、关闭保护 | 仍在 Draft roadmap，不把已有只读 Tab 当成编辑会话 |
| 消息快照父子分支 | 不继续扩展；Draft 线性 revision + CAS 为版本基线，diff/合并按实际需要设计 |
| 从已有群编辑并 apply | 未实现；需要群基线、应用权限和运行中 snapshot 不变的独立契约 |
| 自动保存、本机恢复、草稿列表 | 条件性增强，没有排期或默认承诺 |
| 更多帮助 Bot、自动转交 | 单独按真实需求设计专用 Skill，不扩展通用技能默认上下文 |

聊天控制建群及 frontend-nextgen 接入见 [建群 roadmap](../2026-10-22-collaboration-draft-group-launch/roadmap.md)。现有主屏浮层编辑不代表副屏 Draft 编辑、跨 Bot 写权或 review 已实现。

后续仍以原文为权威：图不反写 YAML，已保存 revision 和历史消息不变。新能力只在有真实需求和独立授权边界时增加，不预建通用文档平台或资源探测流程。
