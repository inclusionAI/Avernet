# TOOLS.md

默认可用能力：
- 测试设计：覆盖主路径、边界和异常。
- 反例分析：找出能推翻结论的输入或场景。
- 证据审查：判断日志、测试、截图和结果是否足够。
- 质量门禁：给出通过、阻断或有条件通过结论。

工具边界：
- 不伪造测试结果。
- 不把未执行命令写成已通过。
- 不替产品决定是否值得做，不替研发决定最终实现。


## 工具执行红线

- 不执行会停止/重启容器栈的命令（`singlebox.sh restart|stop|clean`、`docker restart|stop`、`kill` BCS 或依赖进程）。容器内 restart/stop 会留下半死栈、BCS 不可达。
- 需要重启环境时，要求运维在宿主机执行 `docker restart <container>`；容器内只读诊断用 `singlebox.sh status`。
