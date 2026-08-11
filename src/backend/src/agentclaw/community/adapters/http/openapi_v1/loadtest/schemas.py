"""Payloads for the load-test group.

One model, one field. The point of the group is that the payload is fixed and
trivial — see ``router.py`` — so anything more here would be measured cost with
nothing behind it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HelloWorld(BaseModel):
    """What the load-test HTTP endpoint returns, always."""

    message: str = Field(description='Always the constant "hello world".')
