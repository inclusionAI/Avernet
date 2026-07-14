"""
CapabilityVerify Settings

能力验证服务的配置，支持按环境区分。

从环境变量读取配置，YAML 配置会通过 main.py 的 inject_config_to_env() 注入。
各环境通过 application-{env}.yaml 覆盖默认值。

环境变量：
- CAPABILITY_VERIFY_BCN_CHAT_TOKEN: BCN Chat 鉴权 Token（dev/pre 环境使用）
- CAPABILITY_VERIFY_BCN_CHAT_TOKEN_PROD: BCN Chat 鉴权 Token（prod 环境使用）
- CAPABILITY_VERIFY_BCN_CHAT_COOKIE: BCN Chat Cookie（仅 dev 环境使用）
- CAPABILITY_VERIFY_BCN_CHAT_TIMEOUT: BCN Chat 请求超时（秒）
- CAPABILITY_VERIFY_PROBE_DELAY_SECONDS: 每轮验证间延迟（秒）
- CAPABILITY_VERIFY_MAX_RETRIES: 最大重试次数
- CAPABILITY_VERIFY_TOTAL_TIMEOUT: 整体验证超时（秒）
- CAPABILITY_VERIFY_MAX_CONCURRENT_PROBES: 最大并发验证数
- CAPABILITY_VERIFY_PEER_TOP_K: Peer review 召回 top-K
- CAPABILITY_VERIFY_PEER_MIN_SIMILARITY: Peer review 相似度阈值
- CAPABILITY_VERIFY_PROFILE_ANALYSIS_POLL_INTERVAL: 画像分析轮询间隔（秒）
- CAPABILITY_VERIFY_PROFILE_ANALYSIS_MAX_WAIT: 画像分析最大等待（秒）
- CAPABILITY_VERIFY_DEBUG_OUTPUT_DIR: 调试输出目录
- CAPABILITY_VERIFY_QUEUE_MAX_SIZE: 能力验证队列最大容量
- CAPABILITY_VERIFY_CONSUMER_COUNT: 消费协程数量
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field

from src.utils.env_utils import is_prod


class CapabilityVerifySettings(BaseModel):
    """能力验证服务配置，从环境变量读取。

    Attributes:
        bcn_chat_token: BCN Chat 鉴权 Token（根据环境自动选择 dev/pre 或 prod token）
        bcn_chat_cookie: BCN Chat Cookie（prod/pre 环境不使用）
        bcn_chat_timeout: BCN Chat 请求超时（秒）
        probe_delay_seconds: 每轮验证间延迟（秒）
        max_retries: BCN chat 请求最大重试次数
        total_timeout: 整体验证超时（秒）
        max_concurrent_probes: 最大并发验证数
        peer_top_k: Peer review 召回 top-K 相似 bot
        peer_min_similarity: Peer review 相似度阈值
        profile_analysis_poll_interval: 画像分析轮询间隔（秒）
        profile_analysis_max_wait: 画像分析最大等待（秒）
        debug_output_dir: 调试输出目录
        queue_max_size: 能力验证队列最大容量
        consumer_count: 消费协程数量
    """

    bcn_chat_token: str = Field(default="", description="BCN Chat 鉴权 Token（根据环境自动选择）")
    bcn_chat_cookie: str = Field(default="", description="BCN Chat Cookie（prod/pre 不使用）")
    bcn_chat_timeout: int = Field(default=300, description="BCN Chat 请求超时（秒）")
    probe_delay_seconds: int = Field(default=2, description="每轮验证间延迟（秒）")
    max_retries: int = Field(default=2, description="BCN chat 最大重试次数")
    total_timeout: int = Field(default=600, description="整体验证超时（秒）")
    max_concurrent_probes: int = Field(default=3, description="最大并发验证数")
    peer_top_k: int = Field(default=2, description="Peer review 召回 top-K")
    peer_min_similarity: float = Field(default=0.5, description="Peer review 相似度阈值")
    profile_analysis_poll_interval: float = Field(default=2.0, description="画像分析轮询间隔（秒）")
    profile_analysis_max_wait: float = Field(default=30.0, description="画像分析最大等待（秒）")
    debug_output_dir: str = Field(default="", description="调试输出目录")
    queue_max_size: int = Field(default=1000, description="能力验证队列最大容量")
    consumer_count: int = Field(default=5, description="消费协程数量")

    def __init__(self, **data) -> None:
        env_data = self._load_from_env()
        merged = {**env_data, **data}
        super().__init__(**merged)

    @staticmethod
    def _load_from_env() -> dict:
        """从环境变量加载配置（YAML 配置会通过 main.py 注入到 env）

        bcn_chat_token 按环境自动选择：
        - prod 环境：优先读 CAPABILITY_VERIFY_BCN_CHAT_TOKEN_PROD，回退到 CAPABILITY_VERIFY_BCN_CHAT_TOKEN
        - dev/pre 环境：读 CAPABILITY_VERIFY_BCN_CHAT_TOKEN
        """
        result: dict = {}

        # bcn_chat_token: 根据环境选择不同的 token
        if is_prod():
            prod_token = os.environ.get("CAPABILITY_VERIFY_BCN_CHAT_TOKEN_PROD")
            if prod_token:
                result["bcn_chat_token"] = prod_token.strip()
            else:
                dev_token = os.environ.get("CAPABILITY_VERIFY_BCN_CHAT_TOKEN")
                if dev_token:
                    result["bcn_chat_token"] = dev_token.strip()
        else:
            dev_token = os.environ.get("CAPABILITY_VERIFY_BCN_CHAT_TOKEN")
            if dev_token:
                result["bcn_chat_token"] = dev_token.strip()

        _str_vars = {
            "CAPABILITY_VERIFY_BCN_CHAT_COOKIE": "bcn_chat_cookie",
            "CAPABILITY_VERIFY_DEBUG_OUTPUT_DIR": "debug_output_dir",
        }
        for env_key, field_name in _str_vars.items():
            val = os.environ.get(env_key)
            if val is not None:
                result[field_name] = val.strip()

        _int_vars = {
            "CAPABILITY_VERIFY_BCN_CHAT_TIMEOUT": "bcn_chat_timeout",
            "CAPABILITY_VERIFY_PROBE_DELAY_SECONDS": "probe_delay_seconds",
            "CAPABILITY_VERIFY_MAX_RETRIES": "max_retries",
            "CAPABILITY_VERIFY_TOTAL_TIMEOUT": "total_timeout",
            "CAPABILITY_VERIFY_MAX_CONCURRENT_PROBES": "max_concurrent_probes",
            "CAPABILITY_VERIFY_PEER_TOP_K": "peer_top_k",
            "CAPABILITY_VERIFY_QUEUE_MAX_SIZE": "queue_max_size",
            "CAPABILITY_VERIFY_CONSUMER_COUNT": "consumer_count",
        }
        for env_key, field_name in _int_vars.items():
            val = os.environ.get(env_key)
            if val is not None:
                try:
                    result[field_name] = int(val)
                except ValueError:
                    pass

        _float_vars = {
            "CAPABILITY_VERIFY_PEER_MIN_SIMILARITY": "peer_min_similarity",
            "CAPABILITY_VERIFY_PROFILE_ANALYSIS_POLL_INTERVAL": "profile_analysis_poll_interval",
            "CAPABILITY_VERIFY_PROFILE_ANALYSIS_MAX_WAIT": "profile_analysis_max_wait",
        }
        for env_key, field_name in _float_vars.items():
            val = os.environ.get(env_key)
            if val is not None:
                try:
                    result[field_name] = float(val)
                except ValueError:
                    pass

        return result

    @property
    def is_configured(self) -> bool:
        """配置是否完整（至少有 token 或 cookie）"""
        return bool(self.bcn_chat_token) or bool(self.bcn_chat_cookie)


__all__ = ["CapabilityVerifySettings"]