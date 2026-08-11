#!/usr/bin/env python3
"""Append daily Avernet star metrics to a CSV file."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import ssl
import sys
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback.
    ZoneInfo = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "reports" / "avernet_star_daily.csv"
DEFAULT_RD_ROSTER = Path(__file__).resolve().with_name("avernet_star_rd_team.json")
REPO_OWNER = "inclusionAI"
REPO_NAME = "Avernet"

CSV_FIELDS = [
    "date",
    "observed_at",
    "repo",
    "star_total",
    "rd_star_count",
    "non_rd_star_count",
]

CSV_FIELD_DESCRIPTIONS = {
    "date": "统计日期，按 Asia/Shanghai 时区记录。",
    "observed_at": "实际观测时间，包含 Asia/Shanghai 时区偏移。",
    "repo": "统计的 GitHub 仓库，固定为 inclusionAI/Avernet。",
    "star_total": "GitHub API 返回的仓库 star 总数。",
    "rd_star_count": "研发团队成员中的 star 数，按本地研发名单匹配。",
    "non_rd_star_count": "非研发或未匹配研发名单的 star 数。",
}


def shanghai_now() -> dt.datetime:
    if ZoneInfo is not None:
        return dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai"))


def token_from_shell_files() -> str | None:
    return None


def github_token() -> str:
    token = os.environ.get("GITHUB_PAT_TOKEN") or token_from_shell_files()
    if not token:
        raise RuntimeError(
            "GITHUB_PAT_TOKEN is not set. Export it or save it in a shell profile."
        )
    return token


def github_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if context.cert_store_stats()["x509_ca"] == 0:
        system_ca = Path("/etc/ssl/cert.pem")
        if system_ca.is_file():
            context.load_verify_locations(cafile=str(system_ca))
    return context


def github_api(path: str, token: str) -> object:
    url = f"https://api.github.com/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "avernet-star-stats/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=60, context=github_ssl_context()
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if detail:
            raise RuntimeError(
                f"GitHub API failed: HTTP {exc.code} {exc.reason}: {detail[:500]}"
            ) from exc
        raise RuntimeError(f"GitHub API failed: HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub API returned invalid JSON.") from exc


def github_api_paginated(path: str, token: str) -> list[object]:
    base_url = f"https://api.github.com/{path.lstrip('/')}"
    items: list[object] = []
    next_url: str | None = base_url
    ssl_context = github_ssl_context()
    while next_url:
        request = urllib.request.Request(
            next_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "avernet-star-stats/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=60, context=ssl_context
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, list):
                    raise RuntimeError("GitHub API pagination response shape was not a list.")
                items.extend(payload)
                link = response.headers.get("Link", "")
                next_url = None
                for part in link.split(","):
                    section = part.strip()
                    if 'rel="next"' not in section:
                        continue
                    start = section.find("<")
                    end = section.find(">", start + 1)
                    if start == -1 or end == -1:
                        continue
                    next_url = section[start + 1 : end]
                    break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            if detail:
                raise RuntimeError(
                    f"GitHub API failed: HTTP {exc.code} {exc.reason}: {detail[:500]}"
                ) from exc
            raise RuntimeError(f"GitHub API failed: HTTP {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GitHub API failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("GitHub API returned invalid JSON.") from exc
    return items


def load_rd_team(roster_path: Path) -> dict[str, str]:
    if not roster_path.is_file():
        raise RuntimeError(f"Local RD roster not found: {roster_path}")
    try:
        payload = json.loads(roster_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Local RD roster could not be read: {roster_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Local RD roster is invalid JSON: {roster_path}") from exc
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(
            f"Local RD roster must be a non-empty JSON object: {roster_path}"
        )

    rd_team: dict[str, str] = {}
    for login, nickname in payload.items():
        if not isinstance(login, str) or not login.strip():
            raise RuntimeError(
                f"Local RD roster contains an invalid GitHub login: {login!r}"
            )
        if not isinstance(nickname, str) or not nickname.strip():
            raise RuntimeError(
                f"Local RD roster contains an invalid nickname for {login!r}"
            )
        normalized_login = login.strip().lower()
        normalized_nickname = nickname.strip()
        if normalized_login in rd_team:
            raise RuntimeError(
                f"Local RD roster contains a duplicate GitHub login: {normalized_login}"
            )
        rd_team[normalized_login] = normalized_nickname
    return rd_team


def fetch_stargazers(token: str) -> list[dict[str, object]]:
    data = github_api_paginated(f"repos/{REPO_OWNER}/{REPO_NAME}/stargazers?per_page=100", token)
    users: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict):
            raise RuntimeError("Unexpected stargazers response shape.")
        users.append(item)
    return users


def fetch_star_total(token: str) -> int:
    data = github_api(f"repos/{REPO_OWNER}/{REPO_NAME}", token)
    if not isinstance(data, dict) or "stargazers_count" not in data:
        raise RuntimeError("Unexpected repository response shape.")
    return int(data["stargazers_count"])


def build_row(token: str, rd_roster_path: Path) -> dict[str, str | int]:
    now = shanghai_now()
    rd_team = load_rd_team(rd_roster_path)
    stargazers = fetch_stargazers(token)
    star_total = fetch_star_total(token)
    rd_count = sum(
        1
        for user in stargazers
        if str(user.get("login", "")).lower() in rd_team
    )
    return {
        "date": now.strftime("%Y-%m-%d"),
        "observed_at": now.isoformat(timespec="seconds"),
        "repo": f"{REPO_OWNER}/{REPO_NAME}",
        "star_total": star_total,
        "rd_star_count": rd_count,
        "non_rd_star_count": star_total - rd_count,
    }


def append_row(csv_path: Path, row: dict[str, str | int]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)


def parse_args() -> argparse.Namespace:
    field_help = "\n".join(
        f"{field}: {CSV_FIELD_DESCRIPTIONS[field]}" for field in CSV_FIELDS
    )
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=f"CSV 字段说明:\n{field_help}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path(os.environ.get("AVERNET_STAR_CSV", DEFAULT_CSV)),
        help=f"CSV output path. Default: {DEFAULT_CSV}",
    )
    parser.add_argument(
        "--rd-roster",
        type=Path,
        default=Path(os.environ.get("AVERNET_RD_ROSTER", DEFAULT_RD_ROSTER)),
        help=f"Read-only local RD roster path. Default: {DEFAULT_RD_ROSTER}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print the row without writing the CSV.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        row = build_row(github_token(), args.rd_roster)
        if args.dry_run:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        else:
            append_row(args.csv, row)
            print(f"Appended Avernet star stats to {args.csv}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
