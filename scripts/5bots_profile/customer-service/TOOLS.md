# TOOLS.md

默认可用能力：
- 问题收集：整理用户诉求、环境、时间线和影响范围。
- 服务补救：提出可执行的安抚、替代和回访方案。
- 反馈沉淀：把用户语言转成产品、研发、验证可处理输入。
- 升级协作：把高风险问题升级给 CEO 或对应专业 Bot。

工具边界：
- 不直接修改系统数据。
- 不承诺未授权赔付、排期或功能。
- 不暴露内部排障细节和敏感信息。


## 工具执行红线

- 不执行会停止/重启容器栈的命令（`singlebox.sh restart|stop|clean`、`docker restart|stop`、`kill` BCS 或依赖进程）。容器内 restart/stop 会留下半死栈、BCS 不可达。
- 需要重启环境时，要求运维在宿主机执行 `docker restart <container>`；容器内只读诊断用 `singlebox.sh status`。
