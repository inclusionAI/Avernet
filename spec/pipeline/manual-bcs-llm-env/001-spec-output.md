# Manual 模式 BCS LLM 凭据透传修复

## 背景与问题

`./scripts/singlebox.sh start merchant_hybrid` 选择 `manual` 后，OpenClaw 的模型配置可正确生成，但 BCS 启动时报 `openai-compatible api_key is required`。

根因是 BCS 运行时配置为安全起见会清除模板中的明文 `api_key`；manual 模式仅替换已有的 `api_key_env`。当模板被改为仅包含 `api_key` 时，运行时配置最终不含任何凭据字段，BCS 无法启动。

## 范围

- 使 local/manual BCS 运行时配置始终通过 `OPENCLAW_OPENAI_API_KEY` 环境变量引用凭据。
- 恢复本地 BCS 模板的非敏感默认值，禁止模板保存明文 API key。
- 增加回归测试，覆盖旧式模板含直接 `api_key` 时的安全转换。

不修改 BCS 协议、Provider、Bot profile、用户的 `.env.local`，且不重启现有服务。

## 实施计划

1. 将 local 运行时生成器中的直接 `api_key` 转换为环境变量引用，而非仅删除。
2. 保留 manual 模式对 `api_key_env` 的覆盖，添加低敏凭据来源诊断。
3. 恢复受影响模板为 `api_key_env` 形式，并以临时旧式模板验证生成结果。

## 验收标准

- manual 的三项 `OPENCLAW_OPENAI_*` 环境变量齐全时，两个生成的 BCS runtime TOML 都包含 `api_key_env = "OPENCLAW_OPENAI_API_KEY"`。
- 两个 runtime TOML 均不包含直接 `api_key`，也不含测试用占位 key。
- `scripts/test_hybrid.sh`、相关 shell 语法检查与 `git diff --check` 通过。
