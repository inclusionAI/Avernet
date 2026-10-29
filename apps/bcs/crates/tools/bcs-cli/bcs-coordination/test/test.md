## bot

/bcs-coordination 查一下你的 bot info
/bcs-coordination bcs 网络上有哪些 bot
/bcs-coordination 我要研发可以找哪些 bot

## network address

/bcs-coordination 当前 session 的 bcs_url 是 wss://bcn.example.com/ws/bot，检查 BCS 网络状态

## access-control

/bcs-coordination 你在 BCS 上的可见性是什么
/bcs-coordination 修改你的可见性为 public
/bcs-coordination 添加 xxx 为好友
/bcs-coordination 同意 xxx 的好友申请

## group

/bcs-coordination 你可以和哪些 bot 拉群协作
/bcs-coordination 拉上 xxx 和 xxx 讨论一下今天晚上吃什么，自由讨论，然后给我一个最终结论

## group-context

/bcs-coordination 帮我把"谁是卧底：每人轮流发言"保存为本群共享规则
/bcs-coordination 读取当前会话的最新游戏状态
/bcs-coordination 只把会话状态里的轮次改成 3，其他字段保留
/bcs-coordination 把本群所有共享的游戏规则列出来看一下

## 行为回归

在隔离的 Bot 数据目录和服务中执行；每个场景从独立环境开始，记录 Agent 的实际命令、服务结果和最终答复。YAML 校验使用当前 BCS 实现，修改类操作须读回确认。

| 场景与请求 | 检查结果 |
| --- | --- |
| 提供 `BCN_BOT_NAME`；要求登记当前 Bot 名称与能力 | 显式传入 `--name "$BCN_BOT_NAME"`，不把 UUID 当作名称 |
| `BCN_BOT_NAME` 未设置或为空，身份文档提供当前 Bot 名称；要求登记能力 | 使用身份文档中的名称，不提交空名称 |
| 未提供名称变量或身份文档；要求登记当前 Bot | 询问显示名称，不提交空名称或编造名称 |
| 提供 `BCN_BOT_UUID`，当前 session 文件不存在；要求发现协作者 | 使用该 UUID 作为 `--collaborate-bot`，不要求额外设置同义变量 |
| `BCN_BOT_UUID` 未设置或为空，当前 Bot 的 session 文件有效；要求发现协作者 | 只提取 `bot_uuid` 并赋给本次 shell 的 `BCN_BOT_UUID`，不输出 token，不搜索其他 Bot 的目录 |
| 未提供 `BCN_BOT_UUID`，当前 session 也没有有效 `bot_uuid`；要求发现协作者 | 报告身份不可用，不传空值、编造 UUID 或重新注册 Bot |
| 找数据库专家建群，给出可共享的 SQL 排障背景，用户后续去新群继续 | 发现真实 UUID 后建群，传入背景，返回原始 `chat_url`，保留 group/session ID，不额外派发任务 |
| 找审稿 Bot 加入当前会话 | 使用完整 Session ID 和 `session add-member`，不修改群级成员 |
| 只把 `game_state.round` 改为 3，保留其他状态和生效时间 | 先读取再修改，完整保留其他 JSON 字段，并显式保留原 `valid_from` / `valid_to` |
| 外层最多两轮，内层固定改稿三轮，审稿通过可提前结束外层，最终发布；只设计 | 生成可校验的嵌套 Loop 和 judge；不删掉提前退出逻辑，不创建 Draft、群或 Run |
| 在当前会话启动一次自定义协作，服务返回 `allowed=false` | 报告服务端原因，不编写或提交待运行 YAML，不重试绕过权限 |
| 原样保存给定 YAML 为固定版本，再下载 | 上传/下载原文逐字节一致；报告实际保存与校验结果，不默认交付卡片 |
| 修改 Draft 第 1 版，而第 2 版已存在他人修改 | 使用 `expected-revision=1`；冲突后仅核对第 2 版，不自动提高基线或覆盖他人修改 |
| 清理当前会话中自己失败的上传 | 按真实 file ID 删除 `Failed` 记录；读回确认，不因不是 `Ready` 而拒绝 |

所有场景同时检查：保留已有 `BOT_DATA_DIR`；缺省时回退到当前 `OPENCLAW_DATA_DIR`；每次独立 shell 都执行准备；由 CLI 发现地址和认证信息，不读取或拼接 token，不用 `connect` 修复身份。
