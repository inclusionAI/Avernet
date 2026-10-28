"""Semantic management errors; HTTP mapping belongs to delivery adapters."""


class KnowledgeManagementError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
