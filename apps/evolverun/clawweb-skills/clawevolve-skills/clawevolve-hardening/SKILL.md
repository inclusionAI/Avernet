---
name: clawevolve-hardening
description: 通过平台 HardeningHandler 加固一个已登记 Skill 的候选副本；Handler 保留输入准备、实现选择、结果校验和上报。收到 /clawevolve-hardening 时使用。
---

# Skill 加固

当运行环境通过 Message 投递 `/clawevolve-hardening ...` 时，Agent 只作为原生 Handler 的兼容启动器，不自行选择默认或自定义实现，也不直接修改候选 Skill。BaaS 与 ARCA 的直接 Runner 链路会跳过本启动说明，直接进入同一个 Handler。

把收到的完整 slash command 原文作为一个参数，只运行一次：

```bash
cd clawevolve-hardening && python3 scripts/run.py '<完整 /clawevolve-hardening 命令原文>'
```

等待脚本结束并返回最后的 JSON。不要在脚本运行期间另起加固任务，不要手工调用 Stage Runtime、ClawWeb 接口或 `report.py`，不要在失败后改参数重试。默认实现与 replace 实现的选择、业务 Skill 执行、结果校验和上报均由 Handler 完成。
