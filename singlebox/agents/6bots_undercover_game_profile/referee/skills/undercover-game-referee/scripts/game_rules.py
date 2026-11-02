"""Game rules and safe public projections."""
from __future__ import annotations

import argparse
import json
import re
from typing import Any

__all__ = ['seat_of', 'alive_seats', 'label_of', 'public_name', 'current_round', 'is_pk', 'current_stage', 'speech_seats', 'require_submission', 'submission_scope', 'public_vote_history', 'public_history', 'bluntness_n', 'bluntness_block', 'bigrams', 'check_text', 'parse_vote', 'parse_ballot']

def seat_of(api, state: dict[str, Any], seat: int) -> dict[str, Any]:
    for s in state["seats"]:
        if s["seat"] == seat:
            return s
    api.die("NO_SEAT", f"没有 {seat} 号座位。")
    raise AssertionError("unreachable")


def alive_seats(api, state: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in state["seats"] if s["alive"]]


def label_of(api, state: dict[str, Any], seat: int) -> str:
    """念稿用的称呼：名字后面永远跟着号数。

    人类玩家在副屏里投的是号码，在群里听到的却是名字——中间那次映射一直是他自己
    在做。所以事实层直接把成品给出来，主持稿里每个人第一次出现就用这个。
    """
    s = api.seat_of(state, seat)
    return f"{s['display']}（{s['seat']}号）"


def public_name(api, state: dict[str, Any], seat: int) -> str:
    """写给 Bot 看时的称呼：人类座位不能叫「你」。"""
    s = api.seat_of(state, seat)
    return "人类玩家" if s["kind"] == "human" else s["display"]


def current_round(api, state: dict[str, Any]) -> dict[str, Any]:
    return state["rounds"][-1]


def is_pk(api, state: dict[str, Any]) -> bool:
    return bool(state["rounds"] and api.current_round(state).get("pk"))


def current_stage(api, state: dict[str, Any]) -> dict[str, Any]:
    rnd = api.current_round(state)
    return rnd["pk"] if api.is_pk(state) else rnd


def speech_seats(api, state: dict[str, Any]) -> list[dict[str, Any]]:
    living = sorted(api.alive_seats(state), key=lambda seat: seat["seat"])
    return [seat for seat in living if seat["seat"] in api.current_stage(state)["order"]] if api.is_pk(state) else living


def require_submission(api, state: dict[str, Any], args: argparse.Namespace, kind: str) -> None:
    stage = "pk" if api.is_pk(state) else "regular"
    current = state.get("recovery", {}).get("current")
    if current and (getattr(args, "round", None) is None or getattr(args, "attempt", None) is None):
        api.die("MISSING_SUBMISSION_SCOPE", "节点提交必须保留 --round/--stage/--attempt；不接受无归属的迟到产物。")
    if current and (current["kind"] != kind or current["scope"] != api._recovery.scope(state, kind)
                    or current["attempt"] != getattr(args, "attempt", None)):
        api.die("STALE_SUBMISSION", "提交不属于当前阶段运行，停止；不念稿、不覆盖记录。")
    if getattr(args, "stage", "regular") != stage:
        api.die("WRONG_STAGE", "提交不属于当前常规/PK 阶段，请使用当前节点指令。")
    attempt = getattr(args, "attempt", None)
    expected = api.current_stage(state).get("renders", {}).get(kind, 1)
    if (api.is_pk(state) or attempt is not None) and attempt != expected:
        api.die("STALE_ATTEMPT", "提交不属于当前运行尝试，请使用当前节点指令。")
    round_number = getattr(args, "round", None)
    if (api.is_pk(state) or round_number is not None) and round_number != state["round"]:
        api.die("STALE_ROUND", "提交不属于当前轮，请使用当前节点指令。")


def submission_scope(api, state: dict[str, Any], kind: str) -> str:
    stage = "pk" if api.is_pk(state) else "regular"
    attempt = api.current_stage(state).get("renders", {}).get(kind, 1)
    return f"--round {state['round']} --stage {stage} --attempt {attempt}"


def public_vote_history(api, state: dict[str, Any]) -> list[dict[str, Any]]:
    history = []
    for rnd in state["rounds"]:
        for stage, record in [("regular", rnd)] + ([("pk", rnd["pk"])] if rnd.get("pk") else []):
            if not record.get("tallied"):
                continue
            history.append({
                "round": rnd["round"], "stage": stage,
                "votes": [{"seatNumber": int(seat), "displayName": api.seat_of(state, int(seat))["display"],
                           "text": vote["display"]} for seat, vote in sorted(record["votes"].items(), key=lambda item: int(item[0]))],
                "counts": [{"seatNumber": int(seat), "votes": count} for seat, count in sorted(record["counts"].items(), key=lambda item: int(item[0]))],
            })
    return history


def public_history(api, state: dict[str, Any]) -> list[dict[str, Any]]:
    """给玩家看的公开发言史，只含可展示文本，不含词与身份。"""
    out = []
    for rnd in state["rounds"]:
        for stage, record in [("regular", rnd)] + ([("pk", rnd["pk"])] if rnd.get("pk") else []):
            if not record["speeches"]:
                continue
            entry = {
                "round": rnd["round"],
                "speeches": [{"seat": int(seat), "player": api.seat_of(state, int(seat))["display"], "text": rec["display"]}
                             for seat, rec in sorted(record["speeches"].items(), key=lambda kv: int(kv[0]))],
            }
            if stage == "pk":
                entry["stage"] = stage
            out.append(entry)
    return out


def bluntness_n(api, rnd: int) -> int:
    return api.BLUNTNESS_LADDER.get(rnd, api.BLUNTNESS_FLOOR)


def bluntness_block(api, rnd: int, first: bool) -> str:
    """Release a fresh clue within the round-specific information limit."""
    n = api.bluntness_n(rnd)
    lines = [f"本轮钝度 {n}：描述至少适用于 {n} 样东西，不下定义、不用类目名词。"]
    if rnd == 1:
        lines.append(f"从「{api.ANGLE_MENU}」选一个角度。")
        lines.append("可带一个属性（形状/材质/大小/场合四选一）；用途和对象不能进同一句。")
    elif rnd == 2:
        lines.append(f"从「{api.ANGLE_MENU}」选一个角度，可加一个使用场合。")
    else:
        lines.append("本轮可以说用途，仍不能等价于词的定义。")
    lines.append("每轮补充一个符合自己词的真实线索，优先选自己和前序玩家尚未说过的角度；避免只说泛泛感受或换词复述。")
    lines.append("你是首发，给出一个可比较的宽泛特征。" if first else "参考前序发言避免重复，信息量以本轮钝度为准，不跟随别人一起说空话。")
    return "\n".join(lines)


def bigrams(api, word: str) -> list[str]:
    """词里全部连续两字片段；两字词返回它自己，一字词返回空。"""
    return [word[i : i + 2] for i in range(len(word) - 1)]


def check_text(api, state: dict[str, Any], seat: int, text: str, max_chars: int) -> tuple[str, str | None]:
    """返回 (可展示文本, 违规原因或 None)。

    泄词判定有三种，都精确、可复现，也和告诉玩家的规则一字不差：
      - 完整词语出现在文本里；
      - 词里任意连续两个字出现在文本里（说了大半个词）；
      - 词的每一个字都在文本里出现过（拆散了说全）。
    单个常用字命中不算违规，否则「其他人」这种话会被误判。
    对家的词只要完整出现也遮蔽，防止串词直接摊牌给人类。
    """
    own = api.seat_of(state, seat)["word"]
    other = state["words"]["undercover"] if own == state["words"]["civilian"] else state["words"]["civilian"]
    display = text
    reason = None

    if own and own in display:
        display = display.replace(own, api.MASK * len(own))
        reason = "说出了自己的词"
    elif own and any(g in display for g in api.bigrams(own)):
        for g in api.bigrams(own):
            display = display.replace(g, api.MASK * len(g))
        reason = "说出了自己词里连续的两个字"
    elif own and all(ch in display for ch in own):
        for ch in set(own):
            display = display.replace(ch, api.MASK)
        reason = "把自己的词拆开说了"

    if other and other in display:
        display = display.replace(other, api.MASK * len(other))
        reason = reason or "说出了对家的词"

    if reason is None and len(text) > max_chars:
        reason = f"超过 {max_chars} 字"

    return display, reason


def parse_vote(api, state: dict[str, Any], voter_seat: int | None, text: str) -> tuple[int | None, str | None]:
    target, note = api.parse_ballot(state, voter_seat, text)
    if api.is_pk(state):
        if note == "弃权":
            return None, "PK 禁止弃权"
        if target is not None and target not in api.current_stage(state)["order"]:
            return None, "投票目标不在 PK 名单"
    return target, note


def parse_ballot(api, state: dict[str, Any], voter_seat: int | None, text: str) -> tuple[int | None, str | None]:
    """Parse structured votes and exact actor IDs before legacy Bot text."""
    if not text or not text.strip():
        return None, "没有内容"
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            structured = json.loads(stripped)
        except (ValueError, TypeError):
            return None, "结构化投票格式错误"
        if not isinstance(structured, dict) or structured.get("kind") != "vote":
            return None, "结构化投票格式错误"
        allowed = {"kind", "target_actor_id", "abstain"}
        if set(structured) - allowed:
            return None, "结构化投票格式错误"
        has_target = "target_actor_id" in structured
        has_abstain = structured.get("abstain") is True
        if has_target == has_abstain:
            return None, "结构化投票必须二选一"
        if has_abstain:
            if structured != {"kind": "vote", "abstain": True}:
                return None, "结构化弃权格式错误"
            return None, "弃权"
        actor_id = structured.get("target_actor_id")
        if not isinstance(actor_id, str) or not actor_id.strip():
            return None, "结构化投票目标无效"
        for seat in api.alive_seats(state):
            if api.actor_id_for(seat, state) == actor_id.strip():
                if seat["seat"] == voter_seat:
                    return None, "投了自己"
                return int(seat["seat"]), None
        return None, "结构化投票目标无效"
    # Tally may strip the JSON envelope. IDs are opaque: never infer a seat
    # from their digits, including when the identified player cannot be voted for.
    for seat in state["seats"]:
        if api.actor_id_for(seat, state) == stripped:
            if seat["seat"] == voter_seat:
                return None, "投了自己"
            if not seat["alive"]:
                return None, "投票目标已出局"
            return int(seat["seat"]), None
    if re.search(r"弃权", text):
        return None, "弃权"
    living = {s["seat"] for s in api.alive_seats(state)}
    def acceptable(n: int) -> bool:
        return n in living and n != voter_seat
    for m in re.finditer(r"投[^0-9零一二两三四五六七八九]{0,4}([0-9]+|[零一二两三四五六七八九])", text):
        tok = m.group(1); n = int(tok) if tok.isdigit() else api.CN_DIGITS[tok]
        if acceptable(n): return n, None
    for seat in state["seats"]:
        if seat["display"] and seat["display"] in text and acceptable(seat["seat"]): return seat["seat"], None
    # Unrecognised ASCII identifier tokens are not numeric ballot text.
    if re.fullmatch(r"[A-Za-z0-9_:-]+", stripped) and re.search(r"[A-Za-z_:-]", stripped):
        return None, "投票目标无效"
    nums = set()
    for tok in re.findall(r"[0-9]+|[零一二两三四五六七八九]", text):
        n = int(tok) if tok.isdigit() else api.CN_DIGITS[tok]
        if acceptable(n): nums.add(n)
    if len(nums) == 1: return nums.pop(), None
    if voter_seat is not None and re.search(rf"投[^0-9]{{0,4}}{voter_seat}\b", text): return None, "投了自己"
    return None, "读不出投给谁"
