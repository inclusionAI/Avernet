# BCS 架构 CI 脚本套件

配合 `ci.enforce.bcs.md` 和 `crates/service-api/CONTEXT.md` 落地。

## 文件清单

```
scripts/ci/
├── arch-check.sh                       # 总入口，跑所有检查
├── ci-env.sh                           # 共享 CI 默认配置，如 BCS 默认 base ref
├── check-deps.sh                       # DEP-1~8  依赖图
├── check-import-rules.sh               # LINT-1   use 路径白名单
├── check-port-purity.sh                # LINT-2   port 不依赖领域类型
├── check-forbidden-symbols.sh          # LINT-3   env / transport 框架
├── check-config-validation.sh          # CFG-1    bootstrap config validation
├── check-trait-naming.sh               # LINT-4   命名后缀
├── check-conformance-entries.sh        # TEST-1   conformance 入口
├── check-protocol-compat.sh            # TEST-2   wire 兼容测试
├── check-pr-gate.sh                    # PR-1     PR 模板检查
├── check-waivers.sh                    # WAIVER-1 waiver 完整性
├── check-public-api.sh                 # API-1    pub API 漂移
├── check-baseline-not-growing.sh       # baseline 只能减不能增
└── baseline/
    ├── forbidden-symbols.txt           # LINT-3 例外（CONTEXT.md §5 偏离）
    ├── trait-naming.txt                # LINT-4 例外
    └── conformance-missing.txt         # TEST-1 例外

docs/waivers/
└── _TEMPLATE.md                        # waiver 模板
```

## 本地运行

以下命令默认在 `<ocb>/src/bcs` 下执行；从仓库根也可以直接运行总入口
`bash src/bcs/scripts/ci/arch-check.sh`。

如果当前分支已经是本地开发分支，例如 `refactor_arch_bcs_hj`：

```bash
# 跑全部
bash scripts/ci/arch-check.sh
```

如果当前在仓库根：

```bash
# 跑全部
bash src/bcs/scripts/ci/arch-check.sh
```

单独跑某一项：

```bash
# 单独跑某一项
bash scripts/ci/check-deps.sh
bash scripts/ci/check-port-purity.sh
```

## Base Ref 配置

`check-baseline-not-growing.sh`、`check-pr-gate.sh`、`check-public-api.sh`
需要知道本次变更的目标分支。默认值集中在 `scripts/ci/ci-env.sh` 的
`BCS_DEFAULT_BASE_REF`。

公司 CI 接入时优先传运行时变量，不要改脚本：

```bash
BCS_BASE_REF=origin/refactor_arch_bcs bash scripts/ci/arch-check.sh
```

## 退出码约定

- `0` PASS
- `77` SKIP（依赖不就绪 / 目录不存在，不阻塞 CI）
- 其他 FAIL

总入口会输出百分制分数：

```text
Score: <N>/100 (constitution-weighted, skip excluded)
```

评分直接依据 `<ocb>/docs/arch/ci.enforce.md` 的 required CI gates 和
`<ocb>/docs/arch/arch.rules.md` 的 rule classification；BCS 的本地映射和
权重表记录在 `docs/arch/ci.enforce.bcs.md`。计算方式为
`passed_weight / (passed_weight + failed_weight) * 100`，`skip` 不计入分母。
分数只用于 review；只要存在 fail，脚本仍返回失败。

## baseline 维护

- 仅减不增。新违规必须修代码，不允许加 baseline。
- 修复后删除对应行；`check-baseline-not-growing.sh` 比对 base 和 HEAD 校验。
- 每条 baseline 必须指向 `CONTEXT.md §5` 的偏离编号。

## 启用阶段

见 `CI.enforce.bcs.md §L`。当前 P0 立即可用，P1/P2/P3 随后续修复自动激活。

## 安装前置工具

```bash
sudo apt install ripgrep jq               # LINT-1~4
cargo install cargo-machete --locked      # DEP-8
cargo install cargo-public-api --locked   # API-1
```

`arch-check.sh` 在缺工具时返回 SKIP（exit 77），不阻塞 CI。
