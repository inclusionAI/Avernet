"""
Shared module-level definitions of the BotService facade.

Mechanically split from ``bot_service.py``; the facade re-exports every name
defined here so historical ``from ...bot_service import X`` imports and
mock-patch targets keep working. Mixin modules import their shared names
from this module as well.
"""

import random
import re
import shutil
import string

from datetime import datetime
from pathlib import Path
from typing import Optional

from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.log import get_logger


logger = get_logger()

# Restart idempotency guard TTL. The full device-allocation path has a p99 of
# ~2 min. A guard row older than this is considered abandoned (holder crashed)
# and is reaped on the next restart attempt so a bot is never permanently
# blocked from restarting.
RESTART_LOCK_TTL_SECONDS = 120
#: How long an unbound ``PROVISIONING`` record may sit before a later
#: ``provision_bot`` call treats the claim as abandoned and takes it over (W8).
#: Longer than step 2 normally takes — a device allocation is seconds to a
#: minute — so a live, slow holder that lost its task lease is not raced; the
#: creation job only reaches this on a re-invocation, and the queue's own
#: deadline bounds the whole creation anyway.
PROVISIONING_CLAIM_STALE_SECONDS = 5 * 60

# Passport refresh is callback-driven and retryable. Keep caller-instance
# fan-out bounded while still attempting every instance before reporting an
# aggregate failure.
_CALLER_REFRESH_MAX_WORKERS = 5

def _compose_operator_context(user_id: str, nick_name: str) -> OperatorContext:
    """构建操作人上下文。"""
    return OperatorContext(
        staff_id=user_id,
        staff=user_id,
        nick_name=nick_name,
        operator_name=nick_name,
        tenant_id="default",
    )

class BotServiceError(Exception):
    """Base exception for bot service errors."""
    pass


class BotInvalidLifecycleStateError(BotServiceError):
    """The requested operation is not allowed in the bot's current state."""

    def __init__(self, *, bot_id: str, current_status: str) -> None:
        self.bot_id = bot_id
        self.current_status = current_status
        super().__init__(
            f"Bot {bot_id} cannot be restarted while status is {current_status}"
        )


class BotOperationNotAllowedError(BotServiceError):
    """The operation is not supported for this bot and never will be.

    Distinct from a transient failure: retrying cannot help, so delivery
    surfaces should report it as a client error rather than a server fault.
    """
    pass


class BotNotFoundError(BotServiceError):
    """Bot not found error."""
    pass


class DeviceAllocationError(BotServiceError):
    """Device allocation error."""
    pass


class BotNameExistsError(BotServiceError):
    """Bot name already exists for owner."""
    pass


class DeviceLimitError(BotServiceError):
    """Device limit exceeded error."""
    pass


class BotInstanceCreationError(BotServiceError):
    """Bot instance creation error."""
    pass


class BotPermissionError(BotServiceError):
    """Bot permission error - user is not the owner."""
    pass


class BotNameInvalidError(BotServiceError):
    """Bot name fails validation (empty / illegal chars / too long)."""
    pass


class BotLimitExceededError(BotServiceError):
    """Owner has reached the maximum number of bots allowed."""
    pass


DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE = (
    "Teclaw Cloud Bot 不能作为 Default Bot，请先创建其他类型的 Bot。"
)


class DefaultBotTeclawNotAllowedError(BotServiceError):
    """Default Bot cannot use a Teclaw Cloud engine."""

    def __init__(self) -> None:
        super().__init__(DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE)

# 仅允许中英文、数字、下划线、中划线、空格，以及产品名/版本说明所需的 +、(、) ；禁止 @ # / 等特殊字符。
_BOT_NAME_MAX_LEN = 32
_BOT_NAME_ALLOWED_RE = re.compile(r"^[\w一-鿿 \-()+]+$", re.UNICODE)

def validate_bot_name(bot_name: Optional[str]) -> str:
    """Validate bot_name; return the trimmed value or raise BotNameInvalidError.

    Rules:
    - must be non-empty after strip
    - length <= 32 characters
    - only Chinese/letters/digits/underscore/hyphen/space and + ( ) allowed
    """
    if bot_name is None:
        raise BotNameInvalidError("Bot 名称不能为空")
    trimmed = bot_name.strip()
    if not trimmed:
        raise BotNameInvalidError("Bot 名称不能为空")
    if len(trimmed) > _BOT_NAME_MAX_LEN:
        raise BotNameInvalidError(
            f"Bot 名称长度不能超过 {_BOT_NAME_MAX_LEN} 字符（当前 {len(trimmed)}）"
        )
    if not _BOT_NAME_ALLOWED_RE.match(trimmed):
        raise BotNameInvalidError(
            "Bot 名称只能包含中英文、数字、下划线、中划线、空格以及 +、(、) ，"
            "不允许 @、# 等特殊字符"
        )
    return trimmed


def _copy_tree_fast(src: Path, dst: Path, symlinks: bool = True) -> None:
    """
    使用 rsync 快速复制目录，优化 NAS 场景下的性能。

    rsync 采用增量同步算法，对于已有文件会跳过，大幅提升 NAS 复制速度。
    如果 rsync 不可用，回退到 shutil.copytree。

    Args:
        src: 源目录路径
        dst: 目标目录路径
        symlinks: 是否保留符号链接（默认 True）
    """
    import subprocess

    if not src.exists():
        raise FileNotFoundError(f"Source directory does not exist: {src}")

    # 首先尝试使用 rsync
    try:
        cmd = ["rsync", "-a", "--delete"]
        if symlinks:
            cmd.append("--links")  # 保留符号链接
        # 源目录以 / 结尾表示复制目录内容而不是目录本身
        cmd.extend([str(src) + "/", str(dst) + "/"])

        logger.info(f"[_copy_tree_fast] Using rsync: {src} -> {dst}")
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"[_copy_tree_fast] Rsync completed: {src} -> {dst}")
        return

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"[_copy_tree_fast] Rsync failed ({e}), falling back to shutil.copytree")

    # Fallback: 使用 shutil.copytree
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=symlinks)


def generate_bot_id(owner_id: str, bot_repository: BotRepository) -> str:
    """Return a globally-unique bot_id.

    Never returns 'default' — that convention is retired. ``bot_repository`` is
    retained in the signature for call-site compatibility; id allocation no
    longer depends on owner history.
    """
    # owner_id retained as the semantic subject (whose bot); not used in id allocation.
    del bot_repository  # unused; kept for call-site compatibility
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{date_part}_{random_part}"
