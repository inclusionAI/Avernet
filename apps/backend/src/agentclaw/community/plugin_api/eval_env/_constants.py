"""评测环境共享常量。

所有评测 Header 名和环境标签键在此统一定义，
消除 core/ 和 plugins/ 之间的重复定义（DRY）。
"""

# 评测标识 Header 名
HEADER_EVAL_ID = "X-Eval-Id"
HEADER_DEFAULT_TAG = "X-Agentclaw-Default-Tag"

# default_tag 在 device_props / extra_envs 中的键名
DYNAMIC_ENV_TAG_KEY = "AGENTCLAW_DEFAULT_TAG"