#!/usr/bin/env python3
"""Governance 候选去重：避免向同一 user+bot 重复提交改进项。

支持两种模式：
  --mode api   : 调用 ClawWeb GET /actions 查询已有项（需 API 已部署）
  --mode local : 对比上次 candidates.json，跳过已提交的 user_id+bot_id
  --mode auto  : 先试 api，失败回退 local（默认）

用法:
  python3 dedup_candidates.py --new candidates.json --old ../20260817/candidates.json
  python3 dedup_candidates.py --new candidates.json --mode api --base-url https://clawweb-pre.alipay.com
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Set

# ── CLI ──────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(description="Governance 候选去重")
    p.add_argument("--new", required=True, help="本次 candidates.json 路径")
    p.add_argument("--old", default=None, help="上次 candidates.json 路径（local 模式用）")
    p.add_argument("--output", default=None, help="去重后输出路径，默认覆盖 --new")
    p.add_argument("--mode", choices=("api", "local", "auto"), default="auto")
    p.add_argument("--base-url", default=None, help="ClawWeb base URL（api 模式用）")
    p.add_argument("--since-days", type=int, default=15, help="api 模式查询天数")
    return p.parse_args()


# ── API 模式 ─────────────────────────────────────────────────────


def query_clawweb(base_url: str, owner_user_id: str, bot_id: str, since_days: int = 15) -> List[dict]:
    """调用 ClawWeb GET /actions 查询同一 user+bot 的已有改进项。"""
    import datetime
    since = (datetime.datetime.now() - datetime.timedelta(days=since_days)).isoformat()[:10]
    params = f"?ownerUserId={owner_user_id}&botId={bot_id}&since={since}T00:00:00%2B08:00&limit=50"
    url = base_url.rstrip("/") + "/api/insight/v1/internal/governance/actions" + params
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("items", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("GET /actions 接口未部署 (404)")
        raise RuntimeError(f"API 查询失败 HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(f"API 请求失败: {e}")


def api_dedup(candidates: List[dict], base_url: str, since_days: int) -> List[dict]:
    """API 模式：逐候选查询 ClawWeb，已存在同根因的 REJECTED/PENDING/IN_PROGRESS 项则跳过。"""
    # 先探测接口是否可用
    try:
        query_clawweb(base_url, candidates[0].get("user_id") or candidates[0].get("ownerUserId"),
                      candidates[0].get("bot_id") or candidates[0].get("botId"), since_days)
    except RuntimeError as e:
        raise  # 接口不可用，抛出让上层回退 local

    kept = []
    dropped = []
    for c in candidates:
        uid = c.get("user_id") or c.get("ownerUserId")
        bid = c.get("bot_id") or c.get("botId")
        if not uid or not bid:
            kept.append(c)
            continue

        items = query_clawweb(base_url, uid, bid, since_days)
        skip = False
        for it in items:
            status = it.get("status", "")
            review = it.get("adminReviewStatus", "")
            if review == "REJECTED" or status in ("PENDING_ADMIN", "IN_PROGRESS"):
                skip = True
                break
        if skip:
            dropped.append(c)
        else:
            kept.append(c)
    print(f"[api] kept={len(kept)} dropped={len(dropped)}", file=sys.stderr)
    return kept


# ── Local 模式 ───────────────────────────────────────────────────


def load_previous_high(old_path: str) -> Set[str]:
    """从上次 candidates.json 提取 HIGH 候选的 user_id:bot_id 集合。"""
    with open(old_path) as f:
        data = json.load(f)
    return {f"{c['user_id']}:{c['bot_id']}" for c in data if c.get("confidence_level") == "HIGH"}


def _get_key(c: dict) -> str:
    """兼容 candidates.json (user_id/bot_id) 和 submit JSON (ownerUserId/botId)。"""
    uid = c.get("user_id") or c.get("ownerUserId")
    bid = c.get("bot_id") or c.get("botId")
    return f"{uid}:{bid}"


def local_dedup(candidates: List[dict], old_path: str) -> List[dict]:
    """Local 模式：对比上次 candidates.json，跳过重叠的 user_id+bot_id。"""
    if not old_path or not os.path.exists(old_path):
        print("[local] 无上次数据，全部保留", file=sys.stderr)
        return candidates

    prev = load_previous_high(old_path)
    kept, dropped = [], []
    for c in candidates:
        key = _get_key(c)
        if key in prev:
            dropped.append(c)
        else:
            kept.append(c)
    print(f"[local] kept={len(kept)} dropped={len(dropped)} (overlap with previous {len(prev)} HIGH)", file=sys.stderr)
    for d in dropped:
        print(f"  skip: {_get_key(d)} — 已在上一批 HIGH 中", file=sys.stderr)
    return kept


# ── Main ─────────────────────────────────────────────────────────


def main():
    args = parse_args()
    with open(args.new) as f:
        candidates = json.load(f)

    high = [c for c in candidates if c.get("confidence_level") == "HIGH"]
    other = [c for c in candidates if c.get("confidence_level") != "HIGH"]
    print(f"输入: {len(candidates)} total, {len(high)} HIGH", file=sys.stderr)

    if args.mode == "api":
        base_url = args.base_url or os.environ.get("CLAWWEB_URL", "https://clawweb.alipay.com")
        kept = api_dedup(high, base_url, args.since_days)
    elif args.mode == "local":
        kept = local_dedup(high, args.old)
    else:  # auto
        base_url = args.base_url or os.environ.get("CLAWWEB_URL", "https://clawweb.alipay.com")
        try:
            kept = api_dedup(high, base_url, args.since_days)
        except Exception:
            print("[auto] API 不可用，回退 local 模式", file=sys.stderr)
            kept = local_dedup(high, args.old)

    # 输出：HIGH 去重后 + 非 HIGH 原样保留
    result = kept + other
    result.sort(key=lambda x: x.get("governance_score", 0), reverse=True)
    print(f"输出: {len(result)} candidates", file=sys.stderr)

    out_path = args.output or args.new
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"写入: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, json.JSONDecodeError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)