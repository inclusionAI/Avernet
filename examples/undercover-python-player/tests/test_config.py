from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from undercover_player.config import PlayerConfig


class PlayerConfigTests(unittest.TestCase):
    def test_model_budget_defaults_support_reasoning_models(self) -> None:
        config = PlayerConfig()
        self.assertGreaterEqual(config.llm_max_tokens, 2048)
        self.assertGreaterEqual(config.llm_max_tokens_ceiling, config.llm_max_tokens)
        self.assertGreaterEqual(config.llm_max_attempts, 1)

    def test_model_budget_env_overrides(self) -> None:
        env = {
            "UNDERCOVER_LLM_MAX_TOKENS": "4096",
            "UNDERCOVER_LLM_MAX_TOKENS_CEILING": "8192",
            "UNDERCOVER_LLM_MAX_ATTEMPTS": "2",
        }
        with patch.dict(os.environ, env, clear=False):
            config = PlayerConfig.from_environment()
        self.assertEqual(config.llm_max_tokens, 4096)
        self.assertEqual(config.llm_max_tokens_ceiling, 8192)
        self.assertEqual(config.llm_max_attempts, 2)

    def test_invalid_model_budget_env_is_rejected(self) -> None:
        with patch.dict(os.environ, {"UNDERCOVER_LLM_MAX_TOKENS": "32"}, clear=False):
            with self.assertRaises(ValueError):
                PlayerConfig.from_environment()
        with patch.dict(
            os.environ,
            {"UNDERCOVER_LLM_MAX_TOKENS": "4096", "UNDERCOVER_LLM_MAX_TOKENS_CEILING": "2048"},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                PlayerConfig.from_environment()
        with patch.dict(os.environ, {"UNDERCOVER_LLM_MAX_ATTEMPTS": "0"}, clear=False):
            with self.assertRaises(ValueError):
                PlayerConfig.from_environment()


if __name__ == "__main__":
    unittest.main()
