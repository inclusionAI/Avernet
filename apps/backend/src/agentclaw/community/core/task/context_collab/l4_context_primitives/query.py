"""L4 Query 参数对象(spec §3.4 表)。

GET 接口的无副作用查询参数,实时构造,不落库;``trunk_events``
为 ``True`` 时查询结果只保留事件 DAG 主事件,默认返回全部可见事件
(spec §6.4)。可见性过滤由 G1 授权接缝执行,不在本对象上判断。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    """查询协作上下文最新状态与任务进度时的参数值对象。"""

    collab_context_id: str
    trunk_events: bool = False
