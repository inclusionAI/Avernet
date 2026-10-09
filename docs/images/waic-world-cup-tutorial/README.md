# WAIC 世界杯教程截图清单

本目录保存 [WAIC 世界杯教程正文](../../waic-live-demo-tutorial.zh-CN.md) 使用的截图。部分截图来自旧版本，启动命令和配置路径以正文为准：从仓库根目录运行 `./singlebox/singlebox.sh`，模型参数写入 `singlebox/.env.local`，Bot profile 位于 `singlebox/agents/6bots_world_cup_creator_profile`。补图时请使用这些当前路径，并同步正文中的图片链接。

| 文件 | 需要截取的画面 | 脱敏与裁剪要求 |
| --- | --- | --- |
| 01-prerequisites-terminal.png | macOS 打开终端，git --version 成功 | 隐藏真实用户名、主机名和私人目录 |
| 02.1-install-tools-complete.png、02.2-install-tools-complete.png | install-tools 完成和关键工具检查通过 | 不显示私有 registry、代理或 token |
| 03-model-config-choice.png | 三种模型配置选择提示 | manual 提示应为 singlebox/.env.local；不截取配置内容或 API Key |
| 04-stack-ready.png | bcs_frontend 启动完成或状态页 | 保留 8000、21000 和 Running 状态 |
| 05-six-bots.png | 6 个 Bot 的 status 输出 | bot_uuid 可打码，保留 Bot 名称和 Running |
| 06-enter-avernet.png | Avernet 首页入口和顶部“世界杯运营总监”视角 | 不显示私人浏览器书签或账号信息 |
| 07.1-custom-collaboration-template.jpg、07.2-custom-collaboration-template.png | “自定义协作”、模板选择、“世界杯比赛前瞻内容生产”和校验结果 | 保留真实按钮文案和“已解析 6 个角色” |
| 08-role-bindings.png | 6 个 participant 与 6 个 Bot 绑定完成 | bot_uuid 不需要出现 |
| 09-new-session.png、09.1-new-session.png、09.2-new-session.png | “新建会话”弹窗与演示输入 | 使用教程中的虚构比赛，不放未发布真实选题 |
| 10.1-running-and-result.png | 节点运行状态与最终发布包 | 不显示模型 API、内部地址或未授权内容 |
| 11-advanced-features.png | 自定义协作的进阶功能 | 不显示私人数据或未授权内容 |

推荐截图宽度至少 1440 像素，浏览器缩放保持 100%。如果一张图无法同时看清节点图和最终内容，可以把第 10 张拆成 10a、10b，并在教程中各加一个链接。
