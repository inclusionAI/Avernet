"""Pagination query parameters for list endpoints.

Used as a FastAPI dependency so `page` / `page_size` show up as query
parameters in the generated OpenAPI, e.g.::

    @router.get("")
    async def list_items(page: PageParamsDep) -> Envelope[Page[Item]]: ...
"""

from typing import Annotated

from fastapi import Depends, Query


class PageParams:
    """Standard 1-based pagination controls shared by all list endpoints."""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
        page_size: Annotated[
            int, Query(ge=1, le=100, description="Items per page (max 100).")
        ] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size


PageParamsDep = Annotated[PageParams, Depends()]
