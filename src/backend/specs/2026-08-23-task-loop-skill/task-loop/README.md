# task-loop 预装 skill 包

任务目标驱动执行闭环的单一预装 skill,整合 7 段(识别 / 规划 / arch 场景规划变体 / 派发搜推 / 验收 / BBS 接力 / 架构师名册 mock),
预装到所有 bot 即等同各段单独安装到对应 bot;每段按各自触发词自门控,仅命中段执行(段5↔段6 arch 接力链路并用为唯一例外)。

## 结构

```
task-loop/
├── SKILL.md          # frontmatter + # task-loop + 路由规则 + 段1..段7
├── references/
│   ├── bbs-task-api.md
│   ├── bbs-judge-rubric.md
│   ├── bbs-idempotency.md
│   ├── recognition-card-format.md
│   └── recognition-platform-protocol.md
└── README.md
```

## 触发与段对照

| 触发 | 段 | 来源真源 |
|---|---|---|
| /task 或 [RESUME_TASK] 或仅副屏标签 | 段1 任务识别 | ~/Desktop/task-recognition/SKILL.md |
| 框架 [planning](非 arch 场景) | 段2 任务规划 | Avernet singlebox_e2e/skills/planning/SKILL.md |
| 框架 [planning] 且 prompt 含「某某某公司」 | 段7 任务规划·arch 场景 | Avernet singlebox_e2e/skills/planning-arch/SKILL.md |
| 框架 [search] | 段3 派发搜推 | Avernet singlebox_e2e/skills/search/SKILL.md |
| 协作群 driver/owner 验收+push | 段4 任务验收 | 部署变体 segments/acceptance-push.md(本地 e2e 用源 acceptance/SKILL.md,poll) |
| 引擎 BBS 通知 | 段5 BBS 接力 | Avernet specs/2026-08-09-.../bbs-relay-single-task/SKILL.md |
| 叶子 instruction 含「某某某公司」 | 段6 架构师名册 mock | Avernet singlebox_e2e/skills/arch-analysis/SKILL.md |

## 再生成

在本 feature dir 运行 `./assemble.sh`,从上述 7 段真源重新拼出 SKILL.md 与 references/。
段体规则:剥各自 frontmatter 取正文,标题统一降一级并入,正文文本与 HR 零改(任务识别段逻辑零改;arch-analysis/planning-arch 段原样嵌入)。
源更新后重跑 assemble.sh 即可对齐。

## 上传 / 预装

将 `task-loop/` 打包上传到 skills,activate 给所有 bot(预装即齐备;arch e2e 场景:owner 用段7 规划、中继 bot 用段5+段6 产架构师名册)。

## 部署变体(预发 / 本地 e2e)

`assemble.py` 常量 `DEPLOY_GATEWAY` 控制变体:置预发网关 `https://teamclawgw-pre.alipay.com` 则把 recognition 段本地联调 URL 重定向到预发(生成预发部署包);置 `None` 则保留源 localhost(本地 e2e 变体)。源 SKILL.md 不动,两变体一键再生。
