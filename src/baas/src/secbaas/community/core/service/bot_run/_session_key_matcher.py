"""SessionKeyMatcher - sessionKey 模糊匹配器

服务端返回的 sessionKey 可能比客户端注册时存储的 key 更长，
会在原始 key 前后追加额外字段。SessionKeyMatcher 封装了模糊匹配逻辑，
当精确匹配失败时，遍历 store 中所有已注册的 key，检查服务端返回的
sessionKey 是否 contains 该 key，从而实现模糊查找。

典型场景：
- 客户端注册 session_key="bcs_grp_e7a255b2:625ddaf6"
- 服务端返回 session_key="agent:claude-code-ws:session:bcs_grp_e7a255b2:625ddaf6:user:claude-code-ws"
- Matcher 通过 contains 匹配，找到 "bcs_grp_e7a255b2:625ddaf6" 对应的 state

注意：contains 模糊匹配采用"找到即返回"策略，遍历 store 中第一个被
sessionKey contains 的 key 即视为匹配成功。
"""

from __future__ import annotations

from dataclasses import dataclass

from secbaas.community.logger import get_logger

from ._session_state import _SessionState

logger = get_logger("core-bot-run")


@dataclass
class _MatchResult:
    """匹配结果。

    Attributes:
        key: 匹配到的原始 key（客户端注册时使用的 key）
        state: 对应的 session state
        matched_by: 匹配方式描述，"exact" 或 "contains"
    """

    key: str
    state: _SessionState
    matched_by: str


class SessionKeyMatcher:
    """sessionKey 模糊匹配器。

    封装 session store 的查找逻辑，支持精确匹配和 contains 模糊匹配。

    查找策略：
    1. 精确匹配：直接在 store 中查找 session_key
    2. contains 模糊匹配：遍历 store 中所有 key，找到第一个被
       session_key contains 的 key 即返回。

    使用示例::

        store: dict[str, _SessionState] = {"bcs_grp_e7a2:625d": state}
        matcher = SessionKeyMatcher(store)

        # 精确匹配
        result = matcher.find("bcs_grp_e7a2:625d")
        # -> MatchResult(key="bcs_grp_e7a2:625d", state=state, matched_by="exact")

        # contains 模糊匹配
        result = matcher.find("agent:claude-code-ws:session:bcs_grp_e7a2:625d:user:x")
        # -> MatchResult(key="bcs_grp_e7a2:625d", state=state, matched_by="contains")

        # 找不到
        result = matcher.find("xyz")
        # -> None
    """

    def __init__(
        self, store: dict[str, _SessionState], ignore_case: bool = False
    ) -> None:
        """初始化匹配器。

        Args:
            store: 被查找的 session store（key 为客户端注册的原始 session_key）
        """
        self._store = store
        self.ignore_case = ignore_case

    def find(self, session_key: str) -> _MatchResult | None:
        """根据 sessionKey 查找对应的 session state。

        查找策略：
        1. 精确匹配：直接在 store 中查找 session_key
        2. contains 模糊匹配：遍历 store 中所有 key，找到第一个被
           session_key contains 的 key 即返回。

        Args:
            session_key: 服务端返回的 sessionKey，可能比客户端注册的 key 长

        Returns:
            匹配结果，包含原始 key、state 和匹配方式；未找到返回 None
        """
        if not session_key:
            return None

        # 1. 精确匹配（O(1)，优先尝试）
        state = self._store.get(session_key)
        if state is not None:
            return _MatchResult(key=session_key, state=state, matched_by="exact")

        # 2. contains 模糊匹配：遍历 store 中的 key，找到第一个被 session_key 包含的 key
        target = session_key.lower() if self.ignore_case else session_key
        for stored_key, stored_state in self._store.items():
            candidate = stored_key.lower() if self.ignore_case else stored_key
            if candidate in target:
                logger.debug(
                    "[SessionKeyMatcher] sessionKey=%s not found in store, "
                    "matched via contains with stored_key=%s",
                    session_key,
                    stored_key,
                )
                return _MatchResult(
                    key=stored_key,
                    state=stored_state,
                    matched_by="contains",
                )

        return None
