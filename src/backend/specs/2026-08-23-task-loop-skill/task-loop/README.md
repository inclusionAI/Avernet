# task-loop 预装 skill 包

    任务目标驱动执行闭环的单一预装 skill,整合 5 段(识别 / 规划 / 派发搜推 / 验收 / BBS 接力),
    预装到所有 bot 即等同各段单独安装到对应 bot;每段按各自触发词自门控,仅命中段执行。

    ## 结构

    ```
    task-loop/
    ├── SKILL.md          # frontmatter + # task-loop + 路由规则 + 段1..段5
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
    | 框架 [planning] | 段2 任务规划 | Avernet singlebox_e2e/skills/planning/SKILL.md |
    | 框架 [search] | 段3 派发搜推 | Avernet singlebox_e2e/skills/search/SKILL.md |
    | worker 叶子自验收 | 段4 任务验收 | Avernet singlebox_e2e/skills/acceptance/SKILL.md |
    | 引擎 BBS 通知 | 段5 BBS 接力 | Avernet specs/2026-08-09-.../bbs-relay-single-task/SKILL.md |

    ## 再生成

    在本 feature dir 运行 `./assemble.sh`,从上述 5 段真源重新拼出 SKILL.md 与 references/。
    段体规则:剥各自 frontmatter 取正文,标题统一降一级并入,正文文本与 HR 零改(任务识别段逻辑零改)。
    源更新后重跑 assemble.sh 即可对齐。

    ## 上传 / 预装

    将 `task-loop/` 打包上传到 skills,activate 给所有 bot(预装即齐备)。
