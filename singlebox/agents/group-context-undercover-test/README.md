# GroupContext 谁是卧底 · 人工测试局

一套 7 Bot 的 OpenClaw profile：1 个裁判 Bot + 5 个玩家 Bot + 1 位人类玩家，共 6 名玩家。
**本 profile 专门用于验证 GroupContext API 的 `add`/`update`/`list`/`get`/`systemset` 接口**，
所有游戏状态通过 GroupContext KV 存储管理，不依赖 Python 脚本。

一个主从协作群可以开多个会话，**每个会话就是独立的一局**。

## 与 6bots_undercover_game_profile 的区别

| 维度 | 原版（6bots） | GroupContext 版（本 profile） |
|------|-------------|---------------------------|
| 状态存储 | Python 脚本 `undercover.py` + 本地文件 | GroupContext API（持久化 KV） |
| 发牌机制 | 脚本随机词对、随机座位 | 裁判通过 `add` 写入 `player_word_1` / `player_word_2` |
| 信息隔离 | 脚本注入节点 instruction | GroupContext `visible_to.userIds` 字段 |
| 状态更新 | 脚本本地文件写入 | GroupContext `update`（supersede 取代链） |
| 玩家查词 | 节点 instruction 注入 | 玩家通过 `bcs-cli group-context get` 查询 |
| 裁判查状态 | 脚本 `status` 命令 | `bcs-cli group-context list` / `get` |

## 角色

| Bot | 角色 | 人格 | GroupContext 权限 |
| --- | --- | --- | --- |
| 裁判Bot | manager / 裁判 | 中立、有仪式感 | 所有条目 `collect_from` |
| 玩家张三 | worker / 潜在卧底 | 活泼冲动型 | `player_word_1` 可见 |
| 玩家李四 | worker / 平民 | 谨慎分析型 | `player_word_2` 可见 |
| 玩家王五 | worker / 平民 | 佛系随和型 | `player_word_2` 可见 |
| 玩家赵六 | worker / 潜在卧底 | 冷峻推理型 | `player_word_1` 可见 |
| 玩家钱七 | worker / 平民 | 戏精型 | `player_word_2` 可见 |

**关键设计**：身份 → 映射（卧底=word_1、平民=word_2）仅记录在 `player_role` 条目中，
`visible_to` 仅裁判，**玩家连"1 是卧底词还是 2 是卧底词"都不知道**。

## GroupContext 游戏条目

| name | scope_level | visible_to | collect_from | 内容 |
|------|-------------|-----------|--------------|------|
| `game_rule` | group | 全员 `[]` | 管理员 | 谁是卧底游戏规则 |
| `player_word_1` | session | 裁判 + 卧底名单 | 裁判 | 你的词语是：香蕉 |
| `player_word_2` | session | 裁判 + 平民名单 | 裁判 | 你的词语是：苹果 |
| `player_role` | session | 仅裁判 | 裁判 | 卧底=..., 平民=..., 卧底词=香蕉(player_word_1)... |
| `player_state` | session | 全员 `[]` | 裁判 | 张三存活、李四存活... |
| `round_speeches_N` | session | 全员 `[]` | 裁判 | 第 N 轮全部玩家发言汇总，供投票前查询 |

## 检验标准

### GroupContext 核心功能

- [ ] **add**：裁判开局新增 4 条 session 级 context（player_word_1/2, player_role, player_state），同 (name, scope) 重复 add 返回 `conflict`
- [ ] **visible_to 隔离**：张三 `list` 只看到 `player_word_1` 不看到 `player_word_2` 和 `player_role`
- [ ] **get 空返回**：玩家 `get player_role` 返回 `items: []`（不在 visible_to 中）
- [ ] **update supersede**：更新 `player_state` 后，旧版本 `valid_to` 回填，新版本 `supersedes` 指向旧版本物理 id
- [ ] **list permission**：玩家 `list` 返回条目带 `permission: "R"`；裁判对 `player_word_1` 返回 `permission: "WR"`
- [ ] **systemset**：管理员写入 `game_rule`（group scope），update-or-create 行为
- [ ] **get 排序**：多 scope 的 get 返回 round → session → group 排序

### 游戏完整性

- [ ] 裁判可以发给 5 位 Bot 玩家，一位都不漏
- [ ] 玩家只能看到自己的词，看不到对方的词
- [ ] 玩家连"自己是卧底还是平民"都不知道
- [ ] 每轮发言 → 写入全员可见 `round_speeches_N` → 玩家查询发言后投票（不能弃权）→ 出局 → 更新 player_state → 下一轮
- [ ] PK 平票流程正确
- [ ] 终局公布真相

## 启动

先起 BCS 和前端：

```bash
./singlebox/singlebox.sh start bcs_frontend
```

再起 6 个 Bot：

```bash
./singlebox/singlebox.sh start bots --profile-dir singlebox/agents/group-context-undercover-test
```

确认全部就绪：

```bash
./singlebox/singlebox.sh status bots --profile-dir singlebox/agents/group-context-undercover-test
```

6 个 Bot 都应显示 `Running`。默认端口 30901 起、步长 10。

## 开一局

1. 打开 <http://127.0.0.1:8000/>，切换到**人类视角**
2. 左侧"拉起协作"：
   - 协作群名称：`GroupContext 谁是卧底`
   - 协作目标：`GroupContext API 人工测试 · 6人局（5 Bot + 我）· 卧底1名`
   - 协作群类型：**任务协作**（主从模式）
   - 群主 Bot：**裁判Bot**
   - 成员 Bot：5 个玩家 Bot
3. 创建后，**在页面底部「用户协作」→「加入当前会话」并确认**
4. 裁判会贴出场白。回复 `开始`

## 信息隔离原理（GroupContext 视角）

```
裁判Bot
├── list → 看到 game_rule(R), player_word_1(WR), player_word_2(WR), player_role(WR), player_state(WR), round_speeches_N(WR)
├── get player_role → ✅ 看得到
│
玩家张三（卧底，在 player_word_1.visible_to 中）
├── list → 看到 game_rule(R), player_word_1(R), player_state(R), round_speeches_N(R)
├── get player_word_1 → ✅ 拿到"香蕉"
├── get player_word_2 → ❌ items: []（不在 visible_to 中）
├── get player_role → ❌ items: []（不在 visible_to 中）
│
玩家李四（平民，在 player_word_2.visible_to 中）
├── list → 看到 game_rule(R), player_word_2(R), player_state(R), round_speeches_N(R)
├── get player_word_2 → ✅ 拿到"苹果"
├── get player_word_1 → ❌ items: []
├── get player_role → ❌ items: []
```

## 目录结构

本 profile 的每个 Bot 目录只保留 singlebox 动态 Bot 系统必需的 3 个 profile 文件；所有实际指令都集中在 `AGENTS.md`，`IDENTITY.md` 和 `KNOWLEDGE.md` 只保留最小说明，避免多文件 prompt 漂移。

```text
group-context-undercover-test/
├── bots.json
├── README.md
├── referee/
│   ├── AGENTS.md      # 裁判完整指令
│   ├── IDENTITY.md    # 必需文件，指向 AGENTS.md
│   └── KNOWLEDGE.md   # 必需文件，指向 AGENTS.md
└── player-{zhangsan,lisi,wangwu,zhaoliu,qianqi}/
    ├── AGENTS.md      # 玩家完整指令
    ├── IDENTITY.md    # 必需文件，指向 AGENTS.md
    └── KNOWLEDGE.md   # 必需文件，指向 AGENTS.md
```

## 参考

- [Group Context API Spec](../../apps/bcs/specs/2026-09-09-group-context-api/spec-v1-publish.md)
- [Tasks](../../apps/bcs/specs/2026-09-09-group-context-api/tasks.md)
- [原版谁是卧底 Profile](../6bots_undercover_game_profile/README.md)