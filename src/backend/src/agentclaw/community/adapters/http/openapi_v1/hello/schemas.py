"""Response model for the hello group."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Hello(BaseModel):
    """The greeting payload; its value is fixed, so a caller can assert on it."""

    message: str = Field(description='Fixed greeting — always "Hello, World!".')
