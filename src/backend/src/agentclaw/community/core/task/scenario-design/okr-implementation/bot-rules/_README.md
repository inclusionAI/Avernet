# OKR-Implementation 各 Bot Rule(V2)

对齐 `task_plan/plans/okr-implementation-relay.yaml`(串行 relay V2)。9 个 role bot 各一份 rule。
链: 8azkbtgs 入口(图前)→ whd6nx7x(营销策略专家,单独①)→ 策略生成群(whd6nx7x driver + 6snfeiq0 + roqtqqkx ②)
→ 风险评估群(7q4cbeze + jbadndne ③)→ f7wfi27d(自动研发 ④)→ 9rgd70li(审核 ⑤)→ k9c2slro(实施 ⑥)→ notify ⑦。

V2 协议见 ../relay-playbook.md §0。要点:
- 产出正文须含 4 块: 背景与目标 / 产出摘要 / 处理不了的问题(每条带背景+给下家目标) / 完整正文;
- 叙述用自然职能词,不写框架拓扑具名(③/④/BBS节点 id),不替下家预写 deliverable 名(圈人分层包/主推池/风险清单/舆情 MVP);
- ① 单独产出后还会在②策略生成群当 driver 一次(单独会话之外);
- 8azkbtgs 入口 rule 含 "大促 OKR→营销策略专家"推断映射;
- ④ 自动研发 rule 含 Carry-forward 硬要求(把上游全料一并写入产出正文供⑤审齐);
- ③ 评估群 driver 强制 `unhandled_tasks` JSON(框架路由④),其余 pass-through:`{"report":"<完整正文 markdown>"}`。
