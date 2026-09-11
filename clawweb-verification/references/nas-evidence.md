# NAS 验收证据读取

## 定位 Bot

```bash
bash /ossfs/workspace/jiangshen_skills/openclaw-nas-locator/locate_bot.sh USER_ID BOT_ID
```

必须检查：

```text
/home/admin/.bot_shared_nas/arca/arcaagentclaw/prod
/home/admin/.bot_shared_nas_2/arca/arcaagentclaw/prod
```

以及：

```text
prod_staff_<USER>_openclaw_<BOT>*
prod_staff_<USER>_claude_code_<BOT>*
```

## Session 位置

```text
$ROOT/.openclaw/agents/<AGENT>/sessions/<uuid>.jsonl
$ROOT/.openclaw/agents/<AGENT>/sessions/<uuid>.trajectory.jsonl
```

普通 `.jsonl` 用于对话与最终结果；trajectory 只在需要工具调用细节时读取。

## 安全读取顺序

1. 枚举文件路径、mtime、字节数；
2. 排除 `.trajectory.jsonl` 和索引文件；
3. 按 `handledAt` 粗筛；
4. 读取 Session 事件中的真实时间；
5. 只提取相关 Task 的用户请求、工具调用、错误和最终回复；
6. 需要时再读取对应 trajectory 的相关节点；
7. 不输出完整 Session、Base64、Cookie、Token 或配置秘钥。

## 当前配置核对

可读取但只提取非敏感事实：

```text
$ROOT/.openclaw/openclaw.json
$ROOT/.openclaw/workspace/AGENTS.md
$ROOT/.openclaw/workspace/TOOLS.md
$ROOT/.openclaw/workspace/skills/**/SKILL.md
$ROOT/.openclaw/mcporter.json
```

配置存在只能证明“修改已落盘”，不能证明运行成功；最终仍需相关新 Session。
