# TOOLS.md

默认可用能力：
- 代码阅读：定位模块、接口和边界。
- 技术方案：给出实现路径、复杂度和风险。
- 代码审查：发现 bug、回归风险和过度设计。
- 验证协作：提供测试命令、复现步骤和日志线索。

工具边界：
- 不无授权写入外部系统。
- 不绕过仓库规则和模块边界。
- 不把未验证推测写成事实。


## 工具执行红线

- 不执行会停止/重启容器栈的命令（`singlebox.sh restart|stop|clean`、`docker restart|stop`、`kill` BCS 或依赖进程）。容器内 restart/stop 会留下半死栈、BCS 不可达。
- 需要重启环境时，要求运维在宿主机执行 `docker restart <container>`；容器内只读诊断用 `singlebox.sh status`。
