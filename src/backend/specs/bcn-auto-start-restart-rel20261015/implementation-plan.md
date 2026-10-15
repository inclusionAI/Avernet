# BCN Lifecycle Implementation Plan

**Goal:** 所有合法 start/restart 自动尝试 BCN，仅 OpenClaw personal 走 plugin。
**Architecture:** 取消 start、同步 BaaS restart、coding BaaS prepare 的旧资格筛选；共享注册 helper 增加 respect_drm=True，create 保留默认，生命周期传 False。复用模式 resolver 和既有 HTTP 协议。
**Spec:** 001-spec-output.md

- [x] 修改旧行为测试并增加 engine/type/template/capability/DRM 矩阵；验证 RED。
- [x] 最小修改三条生命周期路径与 helper，跑聚焦单测验证 GREEN。
- [x] 给既有注册边界增加 request/success/failure 日志、状态与耗时；测试业务字段可见、token 不可见。
- [x] 执行 create/restart 幂等与注册兼容回归、静态检查；生成 002 报告。根 agent 独立评审与全量 CI，管理 Git 与 PR。
