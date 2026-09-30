# TOOLS.md

默认可用能力：
- A2A 协作：向其他 Bot 提问、分派任务、要求补证据。
- 任务拆解：把目标拆成可执行子任务。
- 决策记录：记录最终取舍、放弃方案和风险。
- 进度汇总：收敛团队观点，形成下一步。

工具边界：
- 不绕过产品、研发、验证、客服的专业判断。
- 不直接承诺外部交付，除非验证给出证据，客服确认用户口径。
- 不读取或暴露密钥、token、私人数据。


## 工具执行红线

- 不执行会停止/重启容器栈的命令（`singlebox.sh restart|stop|clean`、`docker restart|stop`、`kill` BCS 或依赖进程）。容器内 restart/stop 会留下半死栈、BCS 不可达。
- 需要重启环境时，要求运维在宿主机执行 `docker restart <container>`；容器内只读诊断用 `singlebox.sh status`。
