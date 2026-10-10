"""Batch result for the Retriever plugin, distinct from a hybrid-search hit."""

from pydantic import BaseModel, ConfigDict, Field

from src.domain.models.candidate_bundle import CandidateBundle
from src.domain.models.retrieval_result import RetrievalExplanation


class CandidateRetrievalResult(BaseModel):
    """Candidate bundle plus diagnostics returned by Retriever.retrieve()."""

    model_config = ConfigDict(extra="forbid")

    candidate_bundle: CandidateBundle
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    explanations: list[RetrievalExplanation] = Field(default_factory=list)
