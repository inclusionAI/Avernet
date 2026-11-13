"""BBS 公开讨论产品协议再导出(模块级交付面,spec §5)。

沿 ``api/task/context_collab_service.py`` 先例:delivery/DI 发现面 re-export
产品协议,HTTP 对外交付后置(P2 裁决 2026-11-13)。模块消费者经
``BbsPublicDiscussionServiceProtocol`` + task_module singleton 使用四用例
(发帖/回帖/列表/详情);操作族映射见 products/service_api.py docstring。
"""

from agentclaw.community.core.task.context_collab.products.service_api import (
    BbsPublicDiscussionServiceProtocol,
)

__all__ = ["BbsPublicDiscussionServiceProtocol"]
