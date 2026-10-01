# clawevolve_diagnose 代码分层

目标：把诊断链路按稳定职责分层，避免业务知识散落在根目录；当前代码只保留真实运行需要的模块，不再保留历史 import facade。

## 当前分层

```text
clawevolve_diagnose/
  cli.py                  # CLI/slash command 解析、打印 JSON、返回退出码
  models.py               # 跨层数据结构
  constants.py            # 代码默认值；不从环境变量赋参
  logger.py               # run-local 日志
  utils.py                # 小型纯函数，后续可继续按 text/json/time 拆分

  acquisition/            # 本地运行环境与 session 获取
    discovery.py          # OpenClaw workspace/session 目录发现
    sessions.py           # JSONL session 流式扫描与轻量解析
    local.py              # 规则候选获取与时间/关键词过滤
    runtime_identity.py   # bot 身份解析
    tracing.py            # 证据补充

  judge/                  # session judge backend、query 重写与诊断映射
    runtime.py            # API-only judge runtime；无 API key 直接失败，subagent 暂时废弃
    openai_chat_client.py       # 标准库流式 Chat Completions、SSE 解析、重试与日志
    openclaw_subagent_client.py # 历史 subagent transport（暂时废弃，仅保留源码参考）
    native_session_analysis.py  # transport-neutral 单 session 输入契约、session evidence 压缩与 Diagnosis 映射
    keyless_session_prompt.py   # API judge 共用的单 session prompt 与 schema 版本（历史命名）
    keyless_session_analyzer.py # 历史无 API key 的 diagnose-native OpenClaw subagent transport（已废弃）
    ocsa_contract.py    # OCSA-first 分析链路的稳定协议标识
    ocsa_session_adapter.py # 完整 Session 到 OCSA 原生输入的无损适配
    ocsa_session_report_analyzer.py # 生产 Session 分析入口，直接运行 OCSA session_report
    ocsa_labels.py       # 保留 OCSA 原始成功/错误标签并派生 good/bad
    ocsa_request_matcher.py # Diagnose 外层 intent 匹配、评测资格与忠实 query 构造
    local_session_judge_provider.py # 本地 session 批次调度、失败隔离、去重与配额控制
    query_rewriter.py     # 上下文无关 eval query 生成；主要供 legacy/兼容调用点使用

  run/                    # diagnose 执行链路编排层
    command.py            # 命令级生命周期：校验、ClawWeb running/final/failed report、真实链路执行
    ids.py                # task-id/step-id 校验、默认 output_dir 解析；保持 id 原样
    invocation.py         # slash command/message 归一化与 secret 清理
    step_reports.py       # Diagnose step report running/succeeded/failed 上报入口
    payloads.py           # CLI 成功/失败 JSON payload 与 summary 回写
    runner.py             # 真实链路：发现 session -> selected session judge -> 选择 -> 产物 -> summary
    progress.py           # 统一进度日志入口
    sampling.py           # judge 后选择与优化元数据标注
    case_artifacts.py     # case JSONL、逐 case 文件夹、原始 session 拷贝、manifest
    analysis_artifact.py  # analysis report 写入
    plan_handoff.py       # diagnose -> Plan Source 写入与 case artifact 路径补全
    json_io.py            # run 层 JSON 写入基础函数
    summary.py            # summary/effective_parameters 构造

  artifacts/              # 本地诊断产物基础能力
    outputs.py            # JSON/JSONL 记录序列化
    reporting.py          # analysis report 与关联文件定位
    case_ids.py           # 稳定 case id

  integration/            # 外部/下游协议
    clawweb_events.py     # ClawWeb step report 上传
    output.py             # ClawWeb diagnose output 构造
    plan_source.py        # diagnose -> plan-source/v2 producer
```

## 设计边界

- acquisition 不调用 LLM。
- judge 不写最终产物，不上传 ClawWeb；封装 direct API transport（仅 API-key） 的 diagnose-native session 分析、query 重写和诊断映射；保留 OCSA session_report 适配代码供兼容/参考。
- artifacts 不做 session 发现和 judge/API 调用。
- integration 只处理协议格式和上传，不参与选择策略。
- cli.py 只负责 argv/argparse/print/exit；命令生命周期在 run/command.py；id/message/payload/report 分别在 run/ids.py、run/invocation.py、run/payloads.py、run/step_reports.py。
- 真实诊断统一由 run/runner.py 编排，不保留离线数据或旁路执行链路。
- run 层只串联链路和写本地产物，不做底层 session 解析、HTTP 协议或 LLM transport 细节；case artifact、analysis report、plan handoff 分文件维护。
