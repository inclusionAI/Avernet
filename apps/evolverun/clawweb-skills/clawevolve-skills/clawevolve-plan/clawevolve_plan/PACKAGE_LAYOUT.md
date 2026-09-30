# clawevolve_plan 代码分层

目标：把 plan 从“单目录平铺”整理为 bench/spec/integration 三个核心域，主 CLI 只负责参数边界和流程编排。

## 当前分层

```text
clawevolve_plan/
  cli.py                  # CLI 参数解析、打印 JSON、返回退出码；不承载业务主流程
  constants.py            # 代码默认值；不从环境变量赋参
  io.py                   # Plan Source 查找、JSON/Markdown 读写基础工具
  logger.py               # run-local 日志；固定 info 输出，不通过环境变量改变行为
  runtime_identity.py     # ClawWeb user/bot 身份文件解析；环境变量推断已禁用

  bench/
    split.py              # train/test 划分、泄漏控制、审计日志
    template_builder.py   # ClawBench task markdown、manifest、zip

  spec/
    builder.py            # objective/spec 机器结构构造
    renderer.py           # objective.md/spec-v0.md 渲染

  pipeline/               # plan 命令执行链路
    runner.py             # 命令级 orchestration：校验、running report、分发 fresh/existing、异常边界
    fresh.py              # 真实生成链路骨架：输入 -> bench -> spec -> final report
    paths.py              # id 校验、默认目录解析；保持 id 原样，不做 -/_ 转换
    inputs.py             # diagnose 输入归档、discovery preflight
    bench_flow.py         # bench template 渲染、ClawWeb domain 上传/复用、bench artifact 落盘
    spec_flow.py          # spec/objective 构建、校验和写入
    existing.py           # 已有结果复用与上传结果缓存复用
    upload.py             # bench domain 上传业务包装与 domain meta
    step_report_payload.py # ClawWeb final report output JSON 构造
    reporting.py          # report 结果日志与命令返回 payload 兼容出口
    artifacts.py          # final artifact publish/skip 封装

  integration/
    clawweb.py            # ClawWeb bench domain 上传与 step report HTTP
    artifact_publish.py   # OSS pack/publish 封装；当前主流程保持 skipped

  pack_skill_core.py      # 暂停启用的 OSS 打包核心，保留为后续恢复入口
```

## 兼容策略

根目录保留 `bench_split.py`、`template_builder.py`、`spec_builder.py`、`renderer.py`、`clawweb.py`、`artifact_publish.py` facade，兼容旧 import。新代码应优先导入 `bench.*`、`spec.*`、`integration.*`。

## 设计边界

- bench 只负责评测集 case/template/split，不生成优化 spec。
- spec 只消费 Planning Context、discovery notes、target 和 bench domain 元信息，不做上传。
- integration 只封装 HTTP/OSS 协议，不决定业务策略。
- cli/main 只负责 argparse/print/exit；命令生命周期在 pipeline/runner.py。
- pipeline/fresh.py 只保留新 plan 生成骨架；bench 上传细节在 bench_flow.py，spec 生成细节在 spec_flow.py，step report output 结构在 step_report_payload.py。
- pipeline/paths.py 统一 id 校验与目录解析，禁止隐式环境变量、禁止修改用户传入 id。
