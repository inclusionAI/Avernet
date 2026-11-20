# HiddenBench 疏散路线信息汇合（BCN 适配版）

四位社区负责人通过 BCN 交流路线事实，最后各自选择目的地。沿用现有 `bench.py run --mode local`、Case Markdown、Profile manifest 和代码 Judge 契约。

## 本地运行与评分

从 Avernet 根目录按[案例集合的启动说明](../README.md#本地启动)准备基础服务并运行本案例。四个成员使用 32001、32011、32021、32031 端口，协作时限为 600 秒。

正式完成且证据齐全后，得分为最终选对路线的人数除以 4。超时保留未评分状态，群消息和成员会话仍可用于分析。

## 数据与分工

| 成员 | 运行角色 | 可见资料 |
| --- | --- | --- |
| A / manager | 协调及决策参与者 | 场景、四条公共事实，以及补给车困在隧道的事实 |
| B / member_b | Worker | 同样的公共资料，以及火灾阻断交通的事实 |
| C / member_c | Worker | 同样的公共资料，以及步行道路关闭的事实 |
| D / member_d | Worker | 同样的公共资料，以及车行道路泥石流的事实 |

各自的事实写在自己的 KNOWLEDGE.md，创建 Agent 时由现有整目录复制机制分发；没有共享可修改状态。其他成员的信息应通过 BCN 消息获得。工作区只做逻辑分隔，当前同系统用户下没有强文件隔离。

配套 Profile 至少包含现有规范要求的 AGENTS.md、IDENTITY.md、KNOWLEDGE.md，另有 TOOLS.md；入口只在 bots.json 中声明。Case 的 workspace_files 为空，不引入新字段或专用 API。原始答案只在 Case/评测侧 source 中，不复制给 Agent。

## 与原始 HiddenBench 的关系

- 原任务：id=1，`evacuation_west_city`。
- 论文：Yuxuan Li、Aoi Naito、Hirokazu Shirado，*Systematic Failures in Collective Reasoning under Distributed Information in Multi-Agent LLMs*，ICML 2026。[论文](https://proceedings.mlr.press/v306/li26ej.html)
- 原始数据：[官方数据集](https://huggingface.co/datasets/YuxuanLi1225/HiddenBench)、[固定 Git 版本](https://github.com/Yassellee/HiddenBench_ICML/blob/3be6ca16973e4fb751ffc0dfb7eb11f2d28335d1/data/benchmark.json)。
- 完整原始记录见 `source/hiddenbench-task-1.json`，版本与 SHA256 见 `source/provenance.json`，原 MIT 许可见 `source/LICENSE`。
- 固定四条事实的成员分配；沿用原始背景与事实，移除实验金钱报酬及不确定结束时间描述，增加当前 BCN 所需委托与输出格式约定。
- 采用现有主从组织方式，最终收卷后评分；未实现原版讨论前投票、固定 15 轮实验协议或对等讨论调度。报告应使用“BCN 适配版”，不能与论文完整实验结果直接对标。
- 代码主分采用讨论后成员平均正确率；至少三位选择正确才满足严格多数正确，可由报告列出。没有把多数分与平均分再平均。

本 Case 不强制全员一致。代码只认可真实身份、实际派发 task_id 和正式完成前的最终回复；协调员在自己的消息里列出其他人的答案不能替代那些成员的回复。

## 许可证

HiddenBench 官方数据集标注为 MIT。复制或分发本案例引用的原始资料或其实质性部分时，须随附原版权声明（Copyright (c) 2026 Yuxuan Li）及许可声明。本目录保留了完整许可证，包括免责声明。[随附许可证](source/LICENSE) · [官方数据集](https://huggingface.co/datasets/YuxuanLi1225/HiddenBench)

## 验证

```bash
python3 -m unittest discover \
  -s apps/evolverun/bench-case/bcn-bench-case/task_02_evacuation_routes/tests -v
```

测试使用合成证据验证 Judge，不是模型端到端成绩。覆盖资料分配、满分/部分分、伪造转述、无关任务/身份/时间、最后选择覆盖、无效格式以及证据缺失。模型端到端效果由本地运行产生。
