"""Desktop bot byte-equivalent 测试 — spec §6.5.2 硬门槛.

**测试语义**:确保 desktop bot 走 ``DeviceContextResolver`` 后,拿到的
``conn_info`` 对 v2 desktop 分支(``DeviceService.get_device_connection_v2``
desktop case,L1446-1476)**无任何退化** —— v2 输出的每一个 key 都必须在 new
里出现且值完全相等。

**这是"不退化"语义,不是"严格 key 集相等"**。new 允许携带 v2 没有的扩展字段:
这些是 Phase 1 schema 合同(``docs/superpowers/backend-agentbox-arca-split-guide.md``
§3 + ``baas_conn_info.py`` 的 BAAS-specific section)显式登记的 BAAS-specific
字段(``paas_device_id`` / ``bot_type`` / ``bind_id`` 等),给下游 invoke_http
transport 用 —— 其中 ``paas_device_id`` 和 ``bot_type`` 是 ``skill_center_module``
真实消费的字段(L295 / L306),不是 dead code,从 builder 中删反而会破坏现有功能。

任何 v2 字段缺失或值不同(超出 ``KNOWN_RENAMES`` / ``EXPECTED_REMOVED_FROM_CONN_INFO``)
都失败。**Step 1 后必须绿且贯穿整个 spec 实施 — desktop 是 v2 的核心调用面,
它的输出不退化是这次重构的非协商项**(下游 ``ArcaDeviceSyncPlugin`` /
``BaasDeviceFileSystem`` / data_init / yuque / expert_chat 读
``conn_info[...]`` 不带兜底,key 缺一个就 ``KeyError``)。

为了让 review 看清"哪些 extras 是合法 Phase 1 schema 扩展",``_semantic_eq``
返回 ``info_msgs`` 列出 new 多出的字段,但不计入失败 —— 失败仅当 v2 字段真的
退化(missing / value diff)。

arca case 同时跑 — 防止任何为对齐 desktop 而做的 normalize 把 arca 路径
打破(arca 走 ``device_service.get_device_connection_v2`` 自己,resolver 透传)。
"""
from __future__ import annotations

from typing import Any

import pytest


# 已知 rename:resolver._normalize_schema 把 ``bind_id`` 增补出 ``binding_id``
# 别名(两个 key 同时存在,不删旧)。比对时把 legacy 的 ``binding_id`` 用同名
# 写回 — i.e. nothing to do here。本期不引入实质字段改名,留 dict 占位以
# 显式声明"未来若新增 rename,这里是唯一登记处"。
KNOWN_RENAMES: dict[str, str] = {}


# resolver._normalize_schema 主动从 conn_info 删除的字段:
# - ``device_provider`` 由 ``ctx.provider`` 顶层承载,不再放 dict。
EXPECTED_REMOVED_FROM_CONN_INFO: set[str] = {"device_provider"}


def _semantic_eq(
    legacy: dict[str, Any],
    new: dict[str, Any],
    *,
    renames: dict[str, str] | None = None,
    removed: set[str] | None = None,
) -> tuple[bool, str]:
    """语义对比 — "v2 输出无退化",而非"key 集相等"。

    步骤:
      1. legacy 先按 ``renames`` 重命名(老 key → 新 key)。
      2. 按 ``removed`` 从 legacy 删字段(这些字段被 resolver 主动剥离,
         挪到 ``DeviceContext`` 顶层 — e.g. ``device_provider``)。
      3. 二向比对:legacy missing 在 new、value 不同 —— 任一都失败。
         new 出现的 extra 字段是 Phase 1 schema 扩展(合法演进),
         不计入失败 —— 仅在错误信息里列出供 review 可见。

    失败语义 ≡ "v2 的某个 caller 拿 legacy[k] 用,走 new[k] 时拿不到 / 值不一样"。
    extras ≡ "v2 caller 不读的新字段,加进去无副作用"。
    """
    renames = renames or {}
    removed = removed or set()

    legacy_normalized = dict(legacy)
    for old, new_name in renames.items():
        if old in legacy_normalized:
            legacy_normalized[new_name] = legacy_normalized.pop(old)
    for k in removed:
        legacy_normalized.pop(k, None)

    diff_msgs: list[str] = []
    for k, v in legacy_normalized.items():
        if k not in new:
            diff_msgs.append(f"missing in new: {k}={v!r}")
            continue
        if new[k] != v:
            diff_msgs.append(
                f"value diff: {k} legacy={v!r} new={new[k]!r}"
            )
    # extras 不计入失败,但在调试时打印出来供 review 可见
    extras = sorted(set(new) - set(legacy_normalized))
    if diff_msgs and extras:
        diff_msgs.append(
            f"(info) phase-1 schema extras (not a failure): {extras}"
        )
    return (not diff_msgs, "; ".join(diff_msgs))


@pytest.mark.integration
def test_desktop_bot_conn_info_byte_equivalent_with_v2(
    desktop_bot_seed,
    device_service,
    device_context_resolver,
):
    """desktop bot 走 resolver vs 走旧 v2 — conn_info 等价。"""
    bot_id = desktop_bot_seed.bot_id
    user_id = desktop_bot_seed.user_id
    binding_id = desktop_bot_seed.binding_id

    # ── 旧路径(基线) ────────────────────────────────────────────
    legacy_conn_info = device_service.get_device_connection_v2(
        binding_id=binding_id, user_id=user_id, nick_name=user_id
    )

    # ── 新路径 ─────────────────────────────────────────────────
    ctx = device_context_resolver.resolve_for_bot(bot_id, user_id)

    # ── ctx 顶层字段断言 ──────────────────────────────────────
    assert ctx.provider == "baas", (
        f"desktop bot binding.device_provider 应为 'baas',got {ctx.provider!r}"
    )
    assert ctx.binding_id == binding_id
    assert ctx.bot_id == bot_id
    assert ctx.user_id == user_id

    # ── conn_info 等价 ────────────────────────────────────────
    ok, msg = _semantic_eq(
        legacy_conn_info,
        ctx.conn_info,
        renames=KNOWN_RENAMES,
        removed=EXPECTED_REMOVED_FROM_CONN_INFO,
    )
    assert ok, f"desktop conn_info 不等价: {msg}"


@pytest.mark.integration
def test_arca_bot_conn_info_byte_equivalent_with_v2(
    arca_bot_seed,
    device_service,
    device_context_resolver,
):
    """arca bot 走 resolver vs 走旧 v2 — conn_info 等价(防 arca 退化)。"""
    bot_id = arca_bot_seed.bot_id
    user_id = arca_bot_seed.user_id
    binding_id = arca_bot_seed.binding_id

    legacy_conn_info = device_service.get_device_connection_v2(
        binding_id=binding_id, user_id=user_id, nick_name=user_id
    )

    ctx = device_context_resolver.resolve_for_bot(bot_id, user_id)
    assert ctx.provider == "arca"
    assert ctx.binding_id == binding_id

    ok, msg = _semantic_eq(
        legacy_conn_info,
        ctx.conn_info,
        renames=KNOWN_RENAMES,
        removed=EXPECTED_REMOVED_FROM_CONN_INFO,
    )
    assert ok, f"arca conn_info 不等价: {msg}"
