"""
GRT Vector Knowledge Base Search

Search the TeamClawBot knowledge base via GRT vector database API.
Supports both CLI and programmatic usage.

Usage:
    # CLI
    python3 scripts/grt_search.py --question "Bot权限怎么配置"
    python3 scripts/grt_search.py --question "Bot权限" --top-k 5 --env pre

    # Python API
    from scripts.grt_search import grt_search, GRTSearchResult

    result = grt_search("Bot权限怎么配置")
    for item in result.items:
        print(f"[{item.score:.2f}] {item.title}: {item.content[:100]}")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

log = logging.getLogger("teamclaw.grt")

# Load .env once at module import time (optional — production may not have python-dotenv)
_ENV_PATH = Path(__file__).parent.parent / ".env"
if load_dotenv and _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=True)

# --- Default Config ---

DEFAULT_INSTANCE_NAME = "TeamClawBot"
DEFAULT_INTERFACE_NAME = "ALL_TeamClawBot"
DEFAULT_TOKEN = "52e0eeb1546f04a2d786d729eb7a5b28"
DEFAULT_USER_NAME = "楚生"
DEFAULT_USER_ID = "103892"
DEFAULT_TOP_K = 3
DEFAULT_RANKING_THRESHOLD = 0.01
DEFAULT_VECTOR_THRESHOLD = 0.6
DEFAULT_RANKING_MODEL = "bge-reranker-base"
DEFAULT_ENV = "prod"

URL_PRE = "https://webgw-pre.alipay.com/smartinfrafaas/com.alipay.sofa.function.SOFAFunction/apply/myjf.common.smartinfrafaas.trwrapper.knowledgebase.runservice"
URL_PROD = "https://webgw.alipay.com/smartinfrafaas/com.alipay.sofa.function.SOFAFunction/apply/myjf.common.smartinfrafaas.trwrapper.knowledgebase.runservice"


def _get_config() -> dict[str, str]:
    """Load GRT config — constants first, env vars as override."""
    return {
        "instance_name": os.getenv("GRT_INSTANCE_NAME", DEFAULT_INSTANCE_NAME),
        "interface_name": os.getenv("GRT_INTERFACE_NAME", DEFAULT_INTERFACE_NAME),
        "token": os.getenv("GRT_TOKEN", DEFAULT_TOKEN),
        "user_name": os.getenv("GRT_USER_NAME", DEFAULT_USER_NAME),
        "user_id": os.getenv("GRT_USER_ID", DEFAULT_USER_ID),
        "env": os.getenv("GRT_ENV", DEFAULT_ENV),
    }


# --- Data Classes ---


@dataclass(frozen=True)
class GRTSearchItem:
    """A single search result item from GRT."""

    content: str
    score: float
    title: str = ""
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "score": self.score,
            "title": self.title,
            "source": self.source,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class GRTSearchResult:
    """Result from a GRT search query."""

    query: str
    items: list[GRTSearchItem] = field(default_factory=list)
    total: int = 0
    raw_response: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "total": self.total,
            "items": [item.to_dict() for item in self.items],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @property
    def has_results(self) -> bool:
        return len(self.items) > 0

    def top(self, n: int = 3) -> list[GRTSearchItem]:
        """Return top N items by score."""
        return sorted(self.items, key=lambda x: x.score, reverse=True)[:n]

    def summary(self) -> str:
        """Return a short human-readable summary."""
        if not self.items:
            return f"GRT search '{self.query}': no results found"
        lines = [f"GRT search '{self.query}': {len(self.items)} results"]
        for i, item in enumerate(self.top(3), 1):
            title = item.title or "(untitled)"
            lines.append(f"  {i}. [{item.score:.2f}] {title}: {item.content[:80]}...")
        return "\n".join(lines)


# --- Core Search ---


def _build_request(
    question: str,
    *,
    user_name: str = "",
    user_id: str = "",
    instance_name: str = DEFAULT_INSTANCE_NAME,
    interface_name: str = DEFAULT_INTERFACE_NAME,
    token: str = DEFAULT_TOKEN,
    env: str = DEFAULT_ENV,
    top_k: int = DEFAULT_TOP_K,
    ranking_threshold: float = DEFAULT_RANKING_THRESHOLD,
    vector_threshold: float = DEFAULT_VECTOR_THRESHOLD,
    ranking_model: str = DEFAULT_RANKING_MODEL,
) -> dict[str, Any]:
    """Build the GRT API request payload."""
    return {
        "instanceName": instance_name,
        "token": token,
        "interfaceName": interface_name,
        "userName": user_name,
        "userId": user_id,
        "env": env,
        "param": {
            "question": question,
            "topK": str(top_k),
            "rankingThreshold": str(ranking_threshold),
            "rankingModel": ranking_model,
            "threshold": str(vector_threshold),
        },
    }


def _parse_response(response_text: str, query: str) -> GRTSearchResult:
    """Parse the GRT API response into a GRTSearchResult.

    GRT response structure:
    {
      "code": "OK",
      "message": null,
      "runResult": {
        "question": "...",
        "rewrittenQuery": null,
        "answer": [
          "<JSON string with a/q/score/title/labels/...>",
          ...
        ]
      }
    }
    """
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        log.warning(f"Failed to parse GRT response as JSON: {response_text[:200]}")
        return GRTSearchResult(query=query, raw_response=response_text)

    items: list[GRTSearchItem] = []

    if not isinstance(data, dict):
        return GRTSearchResult(query=query, raw_response=response_text)

    # Check for API-level error
    code = data.get("code")
    if code and str(code) not in ("OK", "200", "0", 0):
        error_msg = data.get("message", data.get("errorMsg", "unknown error"))
        log.warning(f"GRT search failed: code={code}, message={error_msg}")
        return GRTSearchResult(query=query, raw_response=response_text)

    # Extract answer array from runResult
    run_result = data.get("runResult", data)
    answer_list = run_result.get("answer", []) if isinstance(run_result, dict) else []

    for raw_item in answer_list:
        # GRT answer items are JSON strings, parse them
        if isinstance(raw_item, str):
            try:
                item = json.loads(raw_item)
            except json.JSONDecodeError:
                log.debug(f"Skipping non-JSON answer item: {raw_item[:100]}")
                continue
        elif isinstance(raw_item, dict):
            item = raw_item
        else:
            continue

        content = item.get("a", item.get("content", item.get("text", item.get("answer", ""))))
        score = float(item.get("rerankScore", item.get("score", item.get("rankingScore", 0.0))))
        labels = item.get("labels", {})
        title = labels.get("title", item.get("title", item.get("q", "")))
        source = labels.get("source_description", labels.get("url", item.get("ref", "")))
        file_name = item.get("fileName", "")

        metadata = {
            k: v for k, v in item.items()
            if k not in ("a", "content", "text", "answer", "score", "rankingScore", "rerankScore",
                         "title", "q", "ref", "fileName", "labels")
        }
        if file_name:
            metadata["fileName"] = file_name

        items.append(GRTSearchItem(
            content=content,
            score=score,
            title=title,
            source=source,
            metadata=metadata,
        ))

    # Sort by rerank score descending
    items.sort(key=lambda x: x.score, reverse=True)

    return GRTSearchResult(
        query=query,
        items=items,
        total=len(items),
        raw_response=response_text,
    )


def grt_search(
    question: str,
    *,
    user_name: str = "",
    user_id: str = "",
    top_k: int = DEFAULT_TOP_K,
    ranking_threshold: float = DEFAULT_RANKING_THRESHOLD,
    vector_threshold: float = DEFAULT_VECTOR_THRESHOLD,
    ranking_model: str = DEFAULT_RANKING_MODEL,
    env: str | None = None,
    instance_name: str | None = None,
    interface_name: str | None = None,
    token: str | None = None,
) -> GRTSearchResult:
    """Search the GRT vector knowledge base.

    Args:
        question: The search query.
        user_name: Caller's display name.
        user_id: Caller's employee ID.
        top_k: Number of top results to return.
        ranking_threshold: Reranking confidence threshold (filter below this).
        vector_threshold: Vector similarity threshold (filter below this).
        ranking_model: Reranking model name.
        env: Environment ("prod" or "pre"). Defaults to config.
        instance_name: GRT instance name. Defaults to config.
        interface_name: GRT interface name. Defaults to config.
        token: GRT API token. Defaults to config.

    Returns:
        GRTSearchResult with matched items.
    """
    from http_utils import post as _http_post, RequestException

    config = _get_config()

    request = _build_request(
        question,
        user_name=user_name or config["user_name"],
        user_id=user_id or config["user_id"],
        instance_name=instance_name or config["instance_name"],
        interface_name=interface_name or config["interface_name"],
        token=token or config["token"],
        env=env or config["env"],
        top_k=top_k,
        ranking_threshold=ranking_threshold,
        vector_threshold=vector_threshold,
        ranking_model=ranking_model,
    )

    env_val = request["env"]
    url = URL_PRE if env_val in ("PRE", "pre") else URL_PROD

    log.info(f"Searching GRT: question={question!r}, topK={top_k}, env={env_val}")

    try:
        resp = _http_post(
            url,
            json=request,
            headers={
                "content-type": "application/json",
                "x-webgw-appid": "kbsservice",
                "x-webgw-version": "2.0",
            },
            timeout=30,
        )
        response_text = resp.text
    except RequestException as e:
        log.error(f"GRT search request failed: {e}")
        return GRTSearchResult(query=question)

    return _parse_response(response_text, question)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Search GRT vector knowledge base")
    parser.add_argument("--question", "-q", required=True, help="Search query")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help=f"Top K results (default: {DEFAULT_TOP_K})")
    parser.add_argument("--ranking-threshold", type=float, default=DEFAULT_RANKING_THRESHOLD, help="Reranking threshold")
    parser.add_argument("--vector-threshold", type=float, default=DEFAULT_VECTOR_THRESHOLD, help="Vector similarity threshold")
    parser.add_argument("--ranking-model", default=DEFAULT_RANKING_MODEL, help="Reranking model name")
    parser.add_argument("--env", choices=["prod", "pre"], default=DEFAULT_ENV, help="Environment")
    parser.add_argument("--instance-name", default=DEFAULT_INSTANCE_NAME, help="GRT instance name")
    parser.add_argument("--interface-name", default=DEFAULT_INTERFACE_NAME, help="GRT interface name")
    parser.add_argument("--token", default="", help="GRT API token (overrides env var)")
    parser.add_argument("--user-name", default="", help="Caller display name")
    parser.add_argument("--user-id", default="", help="Caller employee ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    result = grt_search(
        question=args.question,
        user_name=args.user_name,
        user_id=args.user_id,
        top_k=args.top_k,
        ranking_threshold=args.ranking_threshold,
        vector_threshold=args.vector_threshold,
        ranking_model=args.ranking_model,
        env=args.env,
        instance_name=args.instance_name or None,
        interface_name=args.interface_name or None,
        token=args.token or None,
    )

    if args.json:
        print(result.to_json())
    else:
        print(result.summary())


if __name__ == "__main__":
    main()