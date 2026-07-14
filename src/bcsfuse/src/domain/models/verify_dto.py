"""Capability Verify DTO 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.models.worker import Capability


class DimensionProbe(BaseModel):
    """单个维度的验证 prompt。"""

    dimension: str = Field(..., description="维度名称")
    probe_prompt: str = Field(..., min_length=1, description="验证 prompt")


class CapabilityProbes(BaseModel):
    """单个能力域的多维度验证 prompt 集合。"""

    capability_name: str = Field(..., description="能力域名称")
    dimensions: list[DimensionProbe] = Field(..., min_length=1, description="维度验证 prompt 列表")


class VerifyData(BaseModel):
    """验证服务所需的输入数据，从 Worker + Profile 构建。"""

    worker_id: str = Field(..., description="Worker ID")
    capabilities: list[Capability] = Field(..., description="声明的能力列表")
    soul_md: str = Field(default="", description="核心身份描述")
    skill_sets: list[dict] = Field(default_factory=list, description="Skill 集合元信息")
    bot_intro: str = Field(default="", description="Bot 自我介绍的回复内容")


class DimensionResult(BaseModel):
    """单个维度的验证结果。"""

    capability_name: str = Field(..., description="能力域名称")
    dimension: str = Field(..., description="维度名称")
    probe_prompt: str = Field(..., description="发送的验证 prompt")
    response_content: str = Field(default="", description="bot 回复内容")
    failed: bool = Field(default=False, description="是否技术性失败（超时/空回复）")


class DimensionJudgment(BaseModel):
    """单个维度的 LLM 评判结果。"""

    capability_name: str = Field(..., description="能力域名称")
    dimension: str = Field(..., description="维度名称")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度 0-1")
    reasoning: str = Field(default="", description="评判推理过程")


class PeerReviewer(BaseModel):
    """相似 bot 作为 peer reviewer。"""

    worker_id: str = Field(..., description="Worker ID")
    bot_uuid: str = Field(..., description="BCN bot UUID (external_id)")
    similarity: float = Field(..., ge=0.0, le=1.0, description="与被测 bot 的相似度")
    overlap_capabilities: list[str] = Field(default_factory=list, description="重合的能力名称")


class PeerReviewItem(BaseModel):
    """Peer review 单轮问答。"""

    question: str = Field(..., description="Peer bot 生成的问题")
    target_capability: str = Field(default="", description="问题针对的能力")
    tested_bot_answer: str = Field(default="", description="被测 bot 的回答")
    peer_evaluation: str = Field(default="", description="Peer bot 的评价")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Peer 评判置信度")


class PeerReviewResult(BaseModel):
    """单个 peer reviewer 的审查结果。"""

    peer_worker_id: str = Field(..., description="Peer reviewer worker ID")
    peer_bot_uuid: str = Field(..., description="Peer reviewer bot UUID")
    similarity: float = Field(..., ge=0.0, le=1.0, description="与被测 bot 的相似度")
    items: list[PeerReviewItem] = Field(default_factory=list, description="问答列表")
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="总体置信度")
    reasoning: str = Field(default="", description="总体评判推理")