# TOOLS.md

- `avernet-collaboration-help`：咨询、编写、诊断和短引用交付。
- `bcs-coordination`：公共 schema、Draft 契约及独立的创建群/执行流程。
- `bcs-cli collaboration draft create <file> --draft-id <uuid>`：上传并校验固定版，记录返回的 ID 和版本。
- `draft get <id> --revision <n> --output <file>` / `draft update <id> <file> --expected-revision <n>`：从固定版修改。
- `draft validate/preview <id> --revision <n>`：重新校验/取得固定版短引用，不写入内容。

沿用 BCS 地址发现和 Bot 认证，不主动设置 localhost 或 `--url`，不读取、打印或复制 token。
CLI 会处理 ws/wss → http/https；连接配置不要求用户加入聊天 Session。
create/update 后忽略其 message，显式调用固定版本的 draft preview；普通回复发送这次 preview 返回的 message，可附说明。保存、校验和消息发送分别判断。不转抄 YAML、不手写标签、不调用 CLI 发送最终消息。
revoke/delete 只在明确管理请求下执行；预览不授权建群、绑定或 Run。
