# #2611 三局混合牌桌公开脱敏证据（速览）

由 `three-games.json` 自动生成；对局日期 2026-11-01，导出日期 2026-11-02。

## 总览

| 局 | 完整会话 ID | 老陈座位/身份 | 轮次 | 结果 | 老陈发言 | 老陈投票 |
| ---: | --- | --- | ---: | --- | ---: | ---: |
| 1 | `bcs_grp_93c843085d6d4c0ab2fae3765e112763:cf21c17e` | 1 号 / 平民 | 1 | 平民胜（卧底已经全部出局，第 1 轮） | 1 | 1 |
| 2 | `bcs_grp_93c843085d6d4c0ab2fae3765e112763:11491aca` | 4 号 / 卧底 | 1 | 平民胜（卧底已经全部出局，第 1 轮） | 1 | 1 |
| 3 | `bcs_grp_93c843085d6d4c0ab2fae3765e112763:eaf8480f` | 6 号 / 平民 | 2 | 平民胜（卧底已经全部出局，第 2 轮） | 2 | 2 |

合计：3 局完成，老陈 4 次发言 / 4 张有效票；实际任务模型源 llm=8；老陈违规 0；其他玩家违规记录 4 条（均为人类结构化票长度误报，票本身有效）。

## 第 1 局

- 会话：`bcs_grp_93c843085d6d4c0ab2fae3765e112763:cf21c17e`
- 老陈：1 号，平民（终局已公开），bot_uuid `bot_9e1372b5`
- 结果：平民胜 / 卧底已经全部出局 / 第 1 轮 / revealed_at `2026-11-01 21:17:58`

| 轮 | 老陈发言（原文） | 字数 | 违规 | 模型源 | 降级 | 任务耗时 | 老陈投票 | 目标 | 票有效 | 违规 | 模型源 | 降级 | 任务耗时 |
| ---: | --- | ---: | --- | --- | --- | ---: | --- | ---: | --- | --- | --- | --- | ---: |
| 1 | 出门前常会摸一下口袋。 | 11 | 无 | llm | False | 2354 ms | 我投3号 | 3 | True | 无 | llm | False | 1497 ms |

辅助任务（不计票）：
- `vote_start`：source=rules，degraded=True，chars=23；不计入投票统计。

其他玩家违规记录（不包含原话/词语）：
- 第 1 轮，4 号（human），vote，记录：`超过 10 字`，目标 3；structured JSON vote length counted against 10-char limit; vote itself was still accepted。

## 第 2 局

- 会话：`bcs_grp_93c843085d6d4c0ab2fae3765e112763:11491aca`
- 老陈：4 号，卧底（终局已公开），bot_uuid `bot_9e1372b5`
- 结果：平民胜 / 卧底已经全部出局 / 第 1 轮 / revealed_at `2026-11-01 21:37:32`

| 轮 | 老陈发言（原文） | 字数 | 违规 | 模型源 | 降级 | 任务耗时 | 老陈投票 | 目标 | 票有效 | 违规 | 模型源 | 降级 | 任务耗时 |
| ---: | --- | ---: | --- | --- | --- | ---: | --- | ---: | --- | --- | --- | --- | ---: |
| 1 | 站得久，周围一直闹哄哄的 | 12 | 无 | llm | False | 4459 ms | 我投3号 | 3 | True | 无 | llm | False | 3548 ms |

其他玩家违规记录（不包含原话/词语）：
- 第 1 轮，3 号（human），vote，记录：`超过 10 字`，目标 4；structured JSON vote length counted against 10-char limit; vote itself was still accepted。

## 第 3 局

- 会话：`bcs_grp_93c843085d6d4c0ab2fae3765e112763:eaf8480f`
- 老陈：6 号，平民（终局已公开），bot_uuid `bot_9e1372b5`
- 结果：平民胜 / 卧底已经全部出局 / 第 2 轮 / revealed_at `2026-11-01 21:41:46`

| 轮 | 老陈发言（原文） | 字数 | 违规 | 模型源 | 降级 | 任务耗时 | 老陈投票 | 目标 | 票有效 | 违规 | 模型源 | 降级 | 任务耗时 |
| ---: | --- | ---: | --- | --- | --- | ---: | --- | ---: | --- | --- | --- | --- | ---: |
| 1 | 用久了边角会有些磨白 | 10 | 无 | llm | False | 8990 ms | 我投2号 | 2 | True | 无 | llm | False | 1846 ms |
| 2 | 翻开是一页页，合上就能放一边 | 14 | 无 | llm | False | 3569 ms | 我投5号 | 5 | True | 无 | llm | False | 3732 ms |

其他玩家违规记录（不包含原话/词语）：
- 第 1 轮，5 号（human），vote，记录：`超过 10 字`，目标 2；structured JSON vote length counted against 10-char limit; vote itself was still accepted。
- 第 2 轮，5 号（human），vote，记录：`超过 10 字`，目标 6；structured JSON vote length counted against 10-char limit; vote itself was still accepted。

## 限制

- 三局都是真实本机 BCS/OpenClaw/模型/人类玩家混合牌桌，不是单元测试或 mock。
- 第一局主持人在发言汇总节点有一次参数形状错误后自动重试；三局均没有人类发送“卡住了”。
- 其他玩家 4 条“超过 10 字”均来自人类副屏结构化投票 JSON 长度，目标仍解析有效；老陈 0 违规。
- `chat.abort`、迟到结果、PK 只有离线单测证据；真实对局未触发这些路径。
- 30 分钟独立接入验证尚未进行；本证据不能替代该验收项。
- 任务耗时来自 JSONL 的收到/完成时间戳推导（旧日志未记录 duration_ms 字段），见 JSON 的 `duration_source`。

完整机读数据见 `three-games.json`，来源/脱敏说明见 `README.md`。
