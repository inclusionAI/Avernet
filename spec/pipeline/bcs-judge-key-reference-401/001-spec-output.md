# BCS Judge API key 引用解析修复

## 问题

在 `merchant_hybrid` 的 manual 模式中，用户可在 `.env.local` 配置
`OPENCLAW_OPENAI_API_KEY=OPENAI_API_KEY`。模型配置准备阶段能解析该引用，
但 BCS Judge 的运行时配置生成阶段可能直接继承字面量 `OPENAI_API_KEY`。
因此 BCS 发往 OpenAI-compatible Judge 的请求使用了占位符，服务端返回
`401 API key not found`。

## 范围

- 仅在生成本地 BCS Judge 运行时配置前，解析上述明确的环境变量引用。
- BCS TOML 继续仅保存 `api_key_env = "OPENCLAW_OPENAI_API_KEY"`，不写入实际
  API key。
- 增加不含凭据正文的诊断日志与回归测试。

不修改 OpenClaw Bot 配置、状态机调度、Provider 注册或用户的 `.env.local`。

## 实施计划

1. 先为 BCS 运行时配置准备新增回归用例：引用可解析时，进程环境获得真实值且
   生成的 TOML 不出现该值。
2. 在 BCS 运行时配置生成入口复用最小的引用解析逻辑；未提供被引用变量时保持
   明确失败，避免把占位符传给 Judge。
3. 执行 focused shell 测试、shell 语法检查和 diff 检查。

## 验收与测试

- `OPENCLAW_OPENAI_API_KEY=OPENAI_API_KEY` 且 `OPENAI_API_KEY` 已设置时，
  `prepare_bcs_runtime_config` 后 `OPENCLAW_OPENAI_API_KEY` 等于引用目标值。
- 生成的两份 BCS TOML 均引用环境变量名，不包含测试 API key 或旧模板明文。
- 引用目标不存在时，配置准备失败并且不生成可误用占位符的 Judge 配置。
- `scripts/test_singlebox_bcs_runtime_config.sh`、关联模型配置测试和 `bash -n`
  全部通过。
