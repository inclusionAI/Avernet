

# 配置
样例：放在 ${代码目录}/config/bcs-config.json
```json
{
  "bind": "0.0.0.0",
  "port": 21000,
  "bots_base_dir": "${代码目录}/bcs_test_tmp",
  "dingtalk_accounts": [
    {
      "account_id": "AIDesktop",
      "client_id": "${client_id}",
      "client_secret": "${client_secret}",
      "robot_code": "${robot_code}",
      "card_template_id": "${card_template_id}",
      "enable_streaming_cards": true,
      "is_default_reply_bot": true,
      "default_bot_id": "openclaw-enterprise",
      "dm_policy": "open",
      "enable_scene_group": true,
      "allowlist": [
        "*"
      ]
    }
  ]
  }
}
```


# 启动
```
./scripts/bcs-manage.sh -h
  ✗ 未知命令: -h
用法: ./scripts/bcs-manage.sh <command> [args...]

BCS 管理:
  start bcs              启动 BCS 服务
  stop bcs               停止 BCS 服务

Bot 管理:
  start bot <name|all>   启动 bot (Reviewer Bot|Demo Owner|Demo Worker|all)
  stop bot <name|all>    停止 bot
  restart bot <name>     重启 bot
  onboard bot <name|all> Bot 注册到 BCS

全局管理:
  start all              启动 BCS + 所有 bot
  stop all               停止 BCS + 所有 bot
  clean                  清空 bcs_test_tmp 目录
  status                 显示所有服务状态

可用的 Bot: Reviewer Bot Demo Owner Demo Worker
```
一键启动：
./scripts/bcs-manage.sh start all
