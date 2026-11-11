"""Cache copies preserve object graphs without sharing mutable request data."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass

from search_core.core.memory_cache import MemoryCache, _copy_value


def group_card():
    member = {"skills": [{"tags": ["finance"], "extra": {"enabled": True}}]}
    return {
        "card": {
            "name": "协作群",
            "composition": {"agents": {"a": member, "b": member}},
            "extra": [None, False, 0, -0.0, 2**80, "😀"],
        }
    }


def test_group_copy_preserves_aliases_but_detaches_from_source():
    source = group_card()
    copied = _copy_value(source)
    assert copied == deepcopy(source)
    members = copied["card"]["composition"]["agents"]
    assert members["a"] is members["b"]
    members["a"]["skills"][0]["tags"].append("legal")
    assert source == group_card()


def test_nested_containers_and_fallback_share_the_same_copy_memo():
    @dataclass
    class OtherValue:
        metadata: dict

    child = {"tags": ["original"]}
    source = {"child": child, "other": OtherValue(child), (1, 2): (child,)}
    copied = _copy_value(source)
    assert copied == deepcopy(source)
    assert copied["child"] is copied["other"].metadata
    assert copied["child"] is copied[(1, 2)][0]
    copied["child"]["tags"].append("changed")
    assert child == {"tags": ["original"]}


def test_container_cycle_and_subclass_deepcopy_are_preserved():
    source = {"items": []}
    source["items"].append(source)
    copied = _copy_value(source)
    assert copied is not source
    assert copied["items"][0] is copied

    class CustomList(list):
        def __deepcopy__(self, memo):
            return CustomList(["custom copy"])

    copied = _copy_value({"value": CustomList(["original"])})
    assert type(copied["value"]) is CustomList
    assert copied["value"] == ["custom copy"]


def test_parallel_cache_readers_and_producer_cannot_mutate_retained_card():
    cache = MemoryCache(enabled=True, ttl_ms=60000, max_entries=8, max_bytes=100000)
    source = group_card()
    cache.put("group", source)
    source["card"]["name"] = "producer changed"

    def read_and_mutate(i):
        result = cache.get("group")
        assert result == group_card()
        result["card"]["composition"]["agents"]["a"]["skills"][0]["tags"].append(str(i))
        return result

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(read_and_mutate, range(20)))
    assert cache.get("group") == group_card()
    assert len({id(r["card"]) for r in results}) == 20


def test_search_result_copy_preserves_scores_aliases_and_mutable_isolation():
    from search_core.api.models import (
        ScoreDetails,
        SearchItem,
        SearchResult,
        StageScore,
    )

    score = StageScore(0.8, 1)
    item = SearchItem(
        "agent",
        "Agent",
        "summary",
        0.8,
        "rrf",
        extensions={"label": "original"},
        score_details=ScoreDetails(keyword=score, rrf=score),
    )
    source = SearchResult((item, item))
    copied = _copy_value(source)
    assert copied == deepcopy(source)
    assert type(copied) is SearchResult
    assert copied is not source
    assert copied.items[0] is copied.items[1]
    assert copied.items[0] is not item
    assert copied.items[0].score_details.keyword is copied.items[0].score_details.rrf
    assert copied.items[0].score_details.keyword is not score
    copied.items[0].extensions["label"] = "reader changed"
    assert item.extensions == {"label": "original"}


def test_result_tuple_cycle_and_subclass_copy_hooks():
    from search_core.api.models import SearchItem, SearchResult

    item = SearchItem("agent", "Agent", "", 1.0, "rrf")
    source = SearchResult((item,))
    item.extensions["cycle"] = source.items
    copied = _copy_value(source)
    assert copied.items[0].extensions["cycle"] is copied.items
    assert copied.items is not source.items

    class CustomItem(SearchItem):
        def __deepcopy__(self, memo):
            return CustomItem(self.item_id, "custom copy", "", self.score, "rrf")

    custom = CustomItem("custom", "original", "", 1.0, "rrf")
    copied = _copy_value(SearchResult((custom, custom)))
    assert type(copied.items[0]) is CustomItem
    assert copied.items[0].name == "custom copy"
    assert copied.items[0] is copied.items[1]
