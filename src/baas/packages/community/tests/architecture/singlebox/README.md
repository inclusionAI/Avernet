# baas singlebox 防腐守卫

把"local 跟 prod/工厂悄悄不对齐"变成 CI 红线。无 nightly、纯 local 单 CI 跑。

## 已有(参考样板)
- `test_sandbox_factory_signature_parity.py` — **local↔prod 签名 parity**。同一
  Selector 里 prod/local 被同处工厂同参数调用,以 prod 为基准要求 local 位置参 >=
  prod(不写死数字,prod 改几参基准自动跟)。拦回归 `abcb52ff5a`(local_proc 漏第二参)。
  - 扩展:新增 sandbox 在 `PLUGIN_GROUPS` 加 `(name, prod_cls, local_cls)`。
  - 验证:回滚 local_proc 单参 → 红;补回 → 绿。

## 待补(五层)
| 层 | 补什么 | 起点 |
|---|---|---|
| S 签名 | 已起;新增 plugin 加进对应 GROUP | 本目录 |
| E0 | CI 真起 singlebox baas + 走一次真 local_proc 创 device | scripts/app.sh --singlebox |
| E | device 创建走真 local_proc(非 mock_paas_success) | tests/e2e/ |
| 去 mock | test_factory 各 plugin 真 new 一次 | tests/unit/.../test_factory.py |

参考 backend 五层:`docs/singlebox-eval/ANTI-ROT-DESIGN.md`。
