"""File router HTTP schemas."""
from __future__ import annotations

from pydantic import BaseModel


class ReadFileRequest(BaseModel):
    file_path: str


class RmTreeRequest(BaseModel):
    target_path: str


class RemovePathRequest(BaseModel):
    target_path: str


class ListDirRequest(BaseModel):
    dir_path: str
    recursive: bool = False


__all__ = [
    "ReadFileRequest",
    "RmTreeRequest",
    "RemovePathRequest",
    "ListDirRequest",
]
