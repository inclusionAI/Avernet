from agentclaw.community.core.config import sofa


def _get_env_dir(env: str) -> str:
    """根据环境获取目录前缀。

    Args:
        env: 环境标识

    Returns:
        目录前缀
    """
    if env == "prod":
        return "aidesktop_prod"
    elif env == "pre":
        return "aidesktop_pre"
    return "aidesktop_dev"


env_dir = f"{_get_env_dir(sofa.server_env)}"
bolt_data_prefix = f"aidesktop/{env_dir}/bolt_data"
bolt_shared_prefix = f"aidesktop/{env_dir}/bolt_shared"


def get_skills_repo_path():
    return f"{bolt_shared_prefix}/skills-repo"


def get_default_skills_repo_path():
    return f"{bolt_shared_prefix}/skills-default"


def get_skills_center_path():
    return f"{bolt_shared_prefix}/skills-center"


def get_bolt_data_path(entity_type: str, entity_id: str, bot_id) -> str:
    return f"{bolt_data_prefix}/{entity_type}_{entity_id}/{bot_id}"


def get_teclaw_bolt_data_prefix() -> str:
    """Teclaw OSS key prefix: ``teclaw/{env}/bolt_data``.

    Teclaw bot files live under a ``teclaw/`` namespace keyed by the raw
    deployment env (``pre`` / ``prod``), in the same OSS bucket as ARCA
    (``ObjectStorageConfig.bucket_name``) but a distinct prefix from the ARCA
    ``bolt_data_prefix`` (``aidesktop/{env_dir}/bolt_data``). The per-bot
    ``{entity_type}_{entity_id}/{bot_id}/{engine}/workspace/...`` layout below is
    identical, so e.g. ``teclaw/prod/bolt_data/staff_u1/bot7/openclaw/workspace/data/foo.csv``.
    """
    return f"teclaw/{sofa.server_env}/bolt_data"


__all__ = [
    "get_skills_repo_path",
    "get_bolt_data_path",
    "get_teclaw_bolt_data_prefix",
    "get_default_skills_repo_path",
    "get_skills_center_path",
]
