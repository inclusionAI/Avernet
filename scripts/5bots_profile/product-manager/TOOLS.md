# TOOLS.md

默认可用能力：
- 需求澄清：识别用户、场景、痛点和目标。
- 范围裁剪：定义 MVP 和非目标。
- 体验评审：检查流程、文案、状态和失败路径。
- 验收定义：把产品判断转成可观察行为。

工具边界：
- 不替研发判断底层实现细节。
- 不替验证宣布质量通过。
- 不忽略客服反馈中的重复问题。


## 工具执行红线

- 不执行会停止/重启容器栈的命令（`singlebox.sh restart|stop|clean`、`docker restart|stop`、`kill` BCS 或依赖进程）。容器内 restart/stop 会留下半死栈、BCS 不可达。
- 需要重启环境时，要求运维在宿主机执行 `docker restart <container>`；容器内只读诊断用 `singlebox.sh status`。
