---
agent: tc-code
status: completed
created: 2026-10-20
iteration: 2
---

# Bot ext CAS Unicode 修复编码报告

## 既有链路、允许点与禁止范围

既有链路是设备alive required consumer → 默认镜像策略持久化 → BotRepository.compare_and_set_ext → SQLAlchemy条件UPDATE。调用方按None重试，持久化失败时不激活Binding。

允许改动仅为Bot CAS方法中的快照编码、匹配谓词和最小诊断，以及真实SQL回归测试。未修改Bot insert/update writer、share_policy、Draft实现、回调、镜像策略服务、重启预算、路由、schema、依赖或运行时配置。未做raw snapshot或通用serializer重构。

Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/fix-bot-ext-cas-unicode-20261020`；分支`fix/bot-ext-cas-unicode-20261020`；基线`b79b220fc1af34244430b2ce06652e6195adad93`。本worker未commit/push/rebase/创建PR或部署。

## 最终实现

`apps/backend/src/agentclaw/community/core/repository/implementations/bot/bot.py`：

- expected_ext非NULL时，参数绑定IN同时匹配`json.dumps(expected_ext, ensure_ascii=False)`与旧默认ASCII转义编码。仍为单条受owner/env/is_delete/快照约束的UPDATE。
- 新ext使用ensure_ascii=False，成功后原文中文不被转义。
- expected_ext=None仍使用SQL IS NULL，未匹配仍返回None；异常传播、提交边界和gmt_modified保持原样。
- CAS结果新增既有logger事件`[bot.ext_cas]`：operation、bot_id、affected_rows、expected_ext_is_null、serialization=unicode。无ext或SQL参数输出，0行只表达未匹配，不声称真实并发。

实现阶段曾验证单一Unicode期望方案。真实历史转义TEXT测试表明该方案会把之前成功的CAS变成None；按主编排调整，只保留CAS局部双编码匹配，撤回本worker临时的insert/update一致化改动，并更新001 spec iteration2。不把旧格式失败作为可接受回归。

Draft CAS经既有ConfigArtifactOffloader.prepare已使用ensure_ascii=False；新增直接SQL测试确认，不修改Draft实现。

## 测试与TDD证据

新增独立`tests/community/repository/bot/test_bot_ext_cas_unicode.py`，复用既有真实文件SQLite fixture及ORM repository，没有mock SQL比较。

1. **RED（原始缺陷）**：直接ORM seed未转义中文键/值TEXT，读取对象后CAS；基线返回None，`assert updated is not None`失败，1 failed。
2. **RED（兼容性）**：对单一Unicode方案直接seed默认转义历史TEXT；CAS返回None，历史兼容测试失败。该结果驱动局部双编码最终方案。
3. **GREEN（最终方案）**：新测试以及完整相邻Bot/Draft repository、镜像策略、restart persistence和activation listener共**151 passed**（2.72秒，21项既有依赖弃用warning）。

覆盖场景：两种中文TEXT均成功、成功后新TEXT保留中文、旧快照拒绝并保持当前内容、ASCII、SQL NULL转中文、非NULL拒绝NULL期望、原insert/update/share_policy行为不变、日志成功/未匹配字段和秘密哨兵不泄露、Draft中文CAS既有能力。

运行目录`apps/backend`，解释器为现成Python3.12环境`/Users/helloworld/Desktop/codes/teamclaw/ocb/apps/backend/.venv/bin/python`，仅复用解释器；`PYTHONPATH=$PWD/src`和pytest.ini路径均指向新worktree。未修改源仓或环境。最初指定Avernet/.venv缺pytest，未用其运行成功结论。

```bash
PYTHONPATH="$PWD/src" /Users/helloworld/Desktop/codes/teamclaw/ocb/apps/backend/.venv/bin/python -m pytest \
  tests/community/repository/bot/test_bot_ext_cas_unicode.py \
  tests/community/repository/bot/test_bot_repository_unified.py \
  tests/community/repository/publishing/test_bot_publish_unified.py \
  tests/community/core/service_bot/services/test_arca_image_pin.py \
  tests/community/core/bot_management/aicoding/test_restart_persistence.py \
  tests/community/core/bot_management/services/test_default_image_policy_listener.py \
  -q --disable-warnings
```

`ruff check`两个修改Python文件：All checks passed；`git diff --check`通过。源文件1000行，新增测试小于1000行，未调整文件规模豁免。原相邻测试文件已1020行，故未继续追加。

正式SAST wrapper已尝试，复用解释器缺flake8，未完成该wrapper的检查；这不等于SAST通过。全量backend、Singlebox、远端ACI及覆盖率门禁由后续独立review/regression/PR流程验证，未在本报告宣称通过或编造覆盖率。

## 兼容与风险

支持Python两种默认ensure_ascii表示，仍非JSON语义比较；任意空白、键顺序、indent及自定义历史格式不在范围。没有扩大身份筛选；继续SQLAlchemy参数绑定，无动态SQL拼接。SQLite真实比较回归不等于生产OceanBase全部collation行为已验证；也没有进行生产恢复验证。

新增诊断字段仅包含身份/结果/编码策略；日志回归携带秘密哨兵验证不出现ext内容。没有出站HTTP边界变化或对应新增请求日志要求。
