"""Template service for managing bot template configurations.

This service handles template creation, retrieval, updates, and deletion.
It integrates with the bot service to manage template configurations.

The ac_templates table currently has: id, bot_id, ext, gmt_create, gmt_modified.
The ext field stores arbitrary user-provided template configuration as a JSON dict.
We only validate that ext is a dict — we do not enforce any internal structure.

Design note: Service method signatures use generic data dicts rather than
hardcoded field-specific parameters, so that when new fields are added to
ac_templates in the future, the repository layer (which already accepts
Dict[str, Any]) can handle them without changing service method signatures.

Permission checks (user_id) are handled at the bot_service layer, not here.
The ac_templates table has no owner_id field, so template-level permission
is inherently tied to bot ownership.

The repository is injected via constructor (DI), following the same pattern
as BotService / BotRepository.
"""

from typing import Optional, Dict, Any

from injector import inject

from agentclaw.community.core.bot_management.token_vault import CIPHER_PREFIX, TokenVault
from agentclaw.community.core.bot_management.repository.template_repository_protocol import TemplateRepository
from agentclaw.community.core.bot_management.engines import resolve_provisioning
from agentclaw.community.log import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TemplateServiceError(Exception):
    """Base exception for template service errors."""
    pass


class TemplateNotFoundError(TemplateServiceError):
    """Template not found error."""
    pass


class TemplateValidationError(TemplateServiceError):
    """Template validation error."""
    pass


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TemplateService:
    """Template service for managing bot template configurations.

    The repository is injected via constructor (DI), following the same
    pattern as BotService / BotRepository.
    """

    @inject
    def __init__(self, repository: TemplateRepository, vault: TokenVault) -> None:
        self._repository = repository
        self._vault = vault

    def _validate_ext_content(self, ext_content: Any) -> None:
        """Validate template ext content (stored in the 'ext' field of ac_templates table).

        The ac_templates table has only bot_id and ext fields. The ext field stores
        arbitrary user-provided template configuration as a JSON dict. Since we
        cannot know the internal structure of ext, we only validate that it is
        a non-empty dict.

        Args:
            ext_content: Content to be stored in ext field

        Raises:
            TemplateValidationError: If validation fails
        """
        if not isinstance(ext_content, dict):
            logger.warning(
                "[template_service._validate_ext_content] ext content is not a dictionary: %s",
                type(ext_content),
            )
            raise TemplateValidationError("Template ext content must be a dictionary")

        if not ext_content:
            logger.warning("[template_service._validate_ext_content] ext content is empty")
            raise TemplateValidationError("Template ext content cannot be empty")

    def _encrypt_token_field(
        self, template_config: Dict[str, Any], template_type: Optional[str]
    ) -> Dict[str, Any]:
        """按引擎策略决定是否加密 token 字段。幂等。

        历史调用链只传 ``template_type``，因此这里通过 registry 的
        ``resolve_for_context`` 做兼容解析；coding 模板规则集中在
        AicodingProvisioningStrategy，TemplateService 不再硬编码具体模板。
        """
        # Legacy call chain only passes template_type; pass empty identity
        # fields (required by BotProvisioningContext) — should_encrypt only
        # consults template_type/template_config.
        ctx, strategy = resolve_provisioning(
            bot_id="",
            owner_id="",
            bot_type="",
            active_engine=None,
            template_type=template_type,
            template_config=template_config,
        )
        if not strategy.should_encrypt_template_token(ctx):
            return template_config
        token = template_config.get("token")
        if not isinstance(token, str) or not token or token.startswith(CIPHER_PREFIX):
            return template_config
        return {**template_config, "token": self._vault.encrypt(token)}

    def create_template(
        self,
        bot_id: str,
        template_config: Dict[str, Any],
        template_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new template record for a bot.

        The ac_templates table currently has bot_id and ext fields.
        template_config is stored in the ext field.
        Permission checks are handled at the bot_service layer.

        Args:
            bot_id: Bot ID
            template_config: Template configuration dictionary (stored in ext field)
            template_type: Optional template type gate; when the engine provisioning
                strategy supports runtime tokens, ``token`` is encrypted before persist.

        Returns:
            Created template record

        Raises:
            TemplateValidationError: If template configuration is invalid
            TemplateServiceError: If template creation fails
        """
        logger.info("[template_service.create_template] Creating template for bot %s", bot_id)

        try:
            # Validate ext content (template_config is stored in the ext field)
            self._validate_ext_content(template_config)
            template_config = self._encrypt_token_field(template_config, template_type)

            template_data = {
                "bot_id": bot_id,
                "ext": template_config,
            }
            template_record = self._repository.insert(template_data)
            logger.info(
                "[template_service.create_template] Template created successfully for bot %s",
                bot_id,
            )
            return template_record
        except TemplateValidationError:
            logger.warning(
                "[template_service.create_template] Validation failed for bot %s",
                bot_id,
            )
            raise
        except Exception as e:
            logger.error(
                "[template_service.create_template] Failed to create template for bot %s: %s",
                bot_id, e, exc_info=True,
            )
            raise TemplateServiceError(f"创建模板失败: {str(e)}")

    def get_template(
        self,
        bot_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get template by bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            Template record or None if not found
        """
        logger.debug("[template_service.get_template] Querying template for bot %s", bot_id)

        try:
            template = self._repository.get_by_bot_id(bot_id)
            if template:
                logger.info("[template_service.get_template] Template found for bot %s", bot_id)
            else:
                logger.debug("[template_service.get_template] Template not found for bot %s", bot_id)
            return template
        except Exception as e:
            logger.error(
                "[template_service.get_template] Failed to get template for bot %s: %s",
                bot_id, e, exc_info=True,
            )
            return None

    def get_template_config(
        self,
        bot_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get template configuration (ext field) by bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            Template configuration dictionary or None if not found
        """
        logger.debug("[template_service.get_template_config] Getting template config for bot %s", bot_id)

        template = self.get_template(bot_id)
        if template:
            config = template.get("ext")
            logger.debug(
                "[template_service.get_template_config] Template config retrieved for bot %s",
                bot_id,
            )
            return config

        logger.debug(
            "[template_service.get_template_config] No template config found for bot %s",
            bot_id,
        )
        return None

    def get_decrypted_codefuse_token(self, bot_id: str) -> Optional[str]:
        """读取 applicationCoding bot 已落库的 codefuse token 并解密为明文 auth_code。

        ``ac_templates.ext.token`` 落库前已由 ``_encrypt_token_field`` 加密
        （``enc:v1:`` 前缀）；这里用 vault 解密回明文，供运行中容器刷新
        ``codefuse.json`` 复用，与 ``DeviceService.apply_device`` 启动时解密、
        ``BaasPublishStatusReconciler._write_codefuse_token_on_success`` 重启时
        解密同路径，避免各消费方各自取值造成密文/明文口径不一致。

        master_key 空（singlebox）时 ``decrypt_or_passthrough`` 退化为原样返回，
        与本地无密钥场景兼容。

        门控与 ``_encrypt_token_field`` 对称：仅 applicationCoding bot 的 token
        才落库加密；调用方（``BotService.update_bot``）应先校验
        对应引擎策略允许 runtime token 后再调用本方法。此处只负责
        取值与解密，前缀缺失时 ``decrypt_or_passthrough`` 原样透传（兼容历史明文）。

        Args:
            bot_id: Bot ID

        Returns:
            明文 codefuse auth_code；无 template / 无 token 时返回 None。
        """
        template_config = self.get_template_config(bot_id)
        if not isinstance(template_config, dict):
            return None

        token = template_config.get("token")
        if not isinstance(token, str) or not token:
            return None
        try:
            return self._vault.decrypt_or_passthrough(token)
        except Exception as e:
            logger.error(
                "[template_service.get_decrypted_codefuse_token] decrypt failed for "
                "bot %s: %s",
                bot_id, e, exc_info=True,
            )
            return None

    def update_template(
        self,
        bot_id: str,
        template_config: Dict[str, Any],
        template_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update template configuration for a bot.

        The ac_templates table currently has bot_id and ext fields.
        template_config is stored in the ext field.
        Permission checks are handled at the bot_service layer.

        Args:
            bot_id: Bot ID
            template_config: New template configuration dictionary
            template_type: Optional template type gate; when the engine provisioning
                strategy supports runtime tokens, ``token`` is encrypted before persist.

        Returns:
            Updated template record or None if not found

        Raises:
            TemplateValidationError: If template configuration is invalid
            TemplateNotFoundError: If template does not exist
            TemplateServiceError: If update fails
        """
        logger.info("[template_service.update_template] Updating template for bot %s", bot_id)

        try:
            # Validate ext content (template_config is stored in the ext field)
            self._validate_ext_content(template_config)
            template_config = self._encrypt_token_field(template_config, template_type)

            # Check if template exists
            if not self.exists_template(bot_id):
                logger.warning(
                    "[template_service.update_template] Template not found for bot %s",
                    bot_id,
                )
                raise TemplateNotFoundError(f"Template not found for bot: {bot_id}")

            update_data = {
                "ext": template_config,
            }
            template_record = self._repository.update_by_bot_id(bot_id, update_data)
            if template_record:
                logger.info(
                    "[template_service.update_template] Template updated successfully for bot %s",
                    bot_id,
                )
            else:
                logger.warning(
                    "[template_service.update_template] Template update returned None for bot %s",
                    bot_id,
                )
            return template_record
        except (TemplateValidationError, TemplateNotFoundError):
            logger.warning(
                "[template_service.update_template] Validation error for bot %s",
                bot_id,
            )
            raise
        except Exception as e:
            logger.error(
                "[template_service.update_template] Failed to update template for bot %s: %s",
                bot_id, e, exc_info=True,
            )
            raise TemplateServiceError(f"更新模板失败: {str(e)}")

    def delete_template(
        self,
        bot_id: str,
    ) -> bool:
        """Delete template by bot_id.

        Permission checks are handled at the bot_service layer.

        Args:
            bot_id: Bot ID

        Returns:
            True if deleted, False if not found

        Raises:
            TemplateServiceError: If deletion fails
        """
        logger.info("[template_service.delete_template] Deleting template for bot %s", bot_id)

        try:
            # Verify template exists
            if not self.exists_template(bot_id):
                logger.warning(
                    "[template_service.delete_template] Template not found for bot %s",
                    bot_id,
                )
                return False

            result = self._repository.delete_by_bot_id(bot_id)
            if result:
                logger.info(
                    "[template_service.delete_template] Template deleted successfully for bot %s",
                    bot_id,
                )
            else:
                logger.warning(
                    "[template_service.delete_template] Template deletion returned False for bot %s",
                    bot_id,
                )
            return result
        except Exception as e:
            logger.error(
                "[template_service.delete_template] Failed to delete template for bot %s: %s",
                bot_id, e, exc_info=True,
            )
            raise TemplateServiceError(f"删除模板失败: {str(e)}")

    def exists_template(self, bot_id: str) -> bool:
        """Check if a template exists for a bot.

        Args:
            bot_id: Bot ID

        Returns:
            True if template exists, False otherwise
        """
        try:
            exists = self._repository.exists_by_bot_id(bot_id)
            logger.debug(
                "[template_service.exists_template] Template exists check for bot %s: %s",
                bot_id, exists,
            )
            return exists
        except Exception as e:
            logger.error(
                "[template_service.exists_template] Failed to check template existence for bot %s: %s",
                bot_id, e, exc_info=True,
            )
            return False

    def list_templates_by_architect_bot_id(self, architect_bot_id: str) -> list:
        """List templates associated with an architect bot.

        Queries ac_templates where ext JSON contains the given architect_bot_id.
        Used to find all application coding bots associated with a domain architect bot.

        Args:
            architect_bot_id: The architect bot's bot_id

        Returns:
            List of template records, each containing bot_id and ext
        """
        try:
            templates = self._repository.list_by_architect_bot_id(architect_bot_id)
            logger.debug(
                "[template_service.list_templates_by_architect_bot_id] Found %d templates for architect bot %s",
                len(templates), architect_bot_id,
            )
            return templates
        except Exception as e:
            logger.error(
                "[template_service.list_templates_by_architect_bot_id] Failed to list templates for architect bot %s: %s",
                architect_bot_id, e, exc_info=True,
            )
            return []

    def create_or_update_template(
        self,
        bot_id: str,
        template_config: Dict[str, Any],
        template_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update template configuration for a bot.

        This method is idempotent - if template already exists, it updates;
        otherwise, it creates a new one.

        Permission checks are handled at the bot_service layer.

        Args:
            bot_id: Bot ID
            template_config: Template configuration dictionary
            template_type: Optional template type gate; when the engine provisioning
                strategy supports runtime tokens, ``token`` is encrypted before persist.

        Returns:
            Created or updated template record

        Raises:
            TemplateValidationError: If template configuration is invalid
            TemplateServiceError: If operation fails
        """
        logger.info(
            "[template_service.create_or_update_template] Creating or updating template for bot %s",
            bot_id,
        )

        try:
            if self.exists_template(bot_id):
                # Update existing template
                logger.debug(
                    "[template_service.create_or_update_template] Template exists, updating for bot %s",
                    bot_id,
                )
                return self.update_template(bot_id, template_config, template_type)
            else:
                # Create new template
                logger.debug(
                    "[template_service.create_or_update_template] Template not found, creating for bot %s",
                    bot_id,
                )
                return self.create_template(bot_id, template_config, template_type)
        except TemplateValidationError:
            logger.warning(
                "[template_service.create_or_update_template] Validation error for bot %s",
                bot_id,
            )
            raise
        except Exception as e:
            logger.error(
                "[template_service.create_or_update_template] Failed to create or update template for bot %s: %s",
                bot_id, e, exc_info=True,
            )
            raise TemplateServiceError(f"创建或更新模板失败: {str(e)}")
