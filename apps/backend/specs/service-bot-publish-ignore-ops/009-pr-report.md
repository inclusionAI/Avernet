# PR 收敛报告：service-bot-publish-ignore-ops

## 范围
- Repo：GitHub inclusionAI/Avernet；worktree：service-bot-publish-ignore-ops-rel20260917。
- Head：feat/service-bot-publish-ignore-ops-rel20260917 / 5cfd442f466a2ac2fdc341d183d50ba192a36d7a。
- Base：REL20260917；原 rebase 点6da0472e，推送时远端已推进到fb15a95e。
- PR：https://github.com/inclusionAI/Avernet/pull/2254（OPEN）。
- Title：feat(service-bot): manage publish ignore rules on exact runtimes。
- Description：Problem / Solution / Validation / Compatibility and risk / Spec；更新依据为 draft 的源码身份/绑定语义及定向测试结果。
- 不合并、不部署、不修改 Docker 启动脚本；无 OCB gitlink 变更。

## 本轮验证
- 本轮按用户最新决定取消version，撤回device_id回退，复用RuntimeBindingResolutionService.resolve(CALLER_SERVICE)及resolve_for_binding_invoke。draft读Bot主binding，verify/online按现有当前阶段规则；共享解析器不改。
- TDD先复现13项无version请求失败，修改后Backend60/Engine44通过；真实DB覆盖draft无发布单、发布阶段绑定区别于主绑定。
- secret scan、Backend/Engine SAST、git diff --check通过。
- 既有请求/成功/失败日志包含stage、version、operator、request_id，设备查询和传输日志包含device_id/binding_id；沿用诊断边界，不记录凭据。
- 当前head本地Backend18796通过/43既有跳过，总覆盖89.17%，增量186/187=99.47%；Engine2708通过/5既有deselected，总覆盖93.50%，增量411/412=99.76%。均以release merge base及90%增量门禁验证通过；Backend既有checker将skips计入case-rate分母。
- 未使用import/变量检查通过；draft真实框架endpoint通过；Docker启动脚本diff为空。
- 远端7项通过，Backend/Singlebox仍PENDING；不能使用旧head门禁代替。
- Unit：https://github.com/inclusionAI/Avernet/actions/runs/35113569194。
- Singlebox：https://github.com/inclusionAI/Avernet/actions/runs/35113569326。
- E2E：https://github.com/inclusionAI/Avernet/actions/runs/35113569210。

## 当前结论
- PR：OPEN。
- 本轮查询reviews/comments/inline comments均为空。
- ACI/CI：PENDING。
- 下一步：确认当前head剩余远端门禁，真实代码失败则修复。本文件为本地证据，不为仅更新证据而再次触发CI。
