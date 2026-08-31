//! Versioned bot registration use case for the BCN V1 API.
//!
//! Mints and verifies short-lived `human_*` register tokens through the
//! shared `bcs_domain` HMAC helpers (same secret, payload format, and TTL as
//! the legacy `GET /register/token` route), so tokens issued by either route
//! are interchangeable. Registration turns a verified token into a newly
//! connected and onboarded bot credential; `POST /register` is anonymous by
//! contract and the token is its only credential.

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_domain::{
    register_token_decode_and_verify, register_token_encode, RegisterTokenPayload,
};
use bcs_service_api::{
    AdminBotOnboardCommand, BotConnectCommand, BotConnectResult, BotManagementService,
    BotOnboardingService, BotStatusUpdateCommand, BotStatusUpdateResult,
    BotUseCaseError, BotVisibilityCommand, BotVisibilityResult, BotLeaveCommand,
    BotLeaveResult, OnboardActorIdentity, ServiceError, SwitchDeliveryToProviderCommand,
    SwitchDeliveryToProviderResult,
};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotRegistration,
    IssueRegisterToken, RegisterBot, RegisterService, RegisterTokenView,
};

/// Register-token lifetime, matching the legacy `GET /register/token` route.
pub const REGISTER_TOKEN_TTL_SECONDS: u64 = 21600; // 6 hours
const REGISTER_TOKEN_NOTE: &str = "Use this token for bot registration within 6 hours";

pub struct RegisterServiceImpl {
    bot_management: Arc<dyn BotManagementService>,
    bot_onboarding: Arc<dyn BotOnboardingService>,
    token_secret: Vec<u8>,
}

impl RegisterServiceImpl {
    pub fn new(
        bot_management: Arc<dyn BotManagementService>,
        bot_onboarding: Arc<dyn BotOnboardingService>,
        token_secret: Vec<u8>,
    ) -> Self {
        Self {
            bot_management,
            bot_onboarding,
            token_secret,
        }
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[async_trait]
impl RegisterService for RegisterServiceImpl {
    async fn issue_register_token(
        &self,
        command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        let user = command.caller.user.as_ref().ok_or_else(|| {
            ApplicationError::forbidden(
                "register token issuance requires an authenticated Human principal",
            )
        })?;
        let payload = RegisterTokenPayload {
            v: 1,
            id: format!("human_{}", user.id),
            exp: now_secs() + REGISTER_TOKEN_TTL_SECONDS,
        };
        Ok(RegisterTokenView {
            token: register_token_encode(&payload, &self.token_secret),
            expires_at: payload.exp * 1000,
            note: REGISTER_TOKEN_NOTE.to_string(),
        })
    }

    async fn register_bot(
        &self,
        command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        let bot_name = command.bot_name.trim();
        let name_len = bot_name.chars().count();
        if name_len < 2 || name_len > 64 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                format!("bot-name must be 2-64 characters, got {name_len}"),
            ));
        }
        let payload = register_token_decode_and_verify(&command.token, &self.token_secret)
            .map_err(|_| ApplicationError::Unauthenticated)?;
        let staff_no = payload.id.trim_start_matches("human_").to_string();
        let connect = self
            .bot_management
            .connect_bot(BotConnectCommand {
                caller_actor_id: None,
                token: None,
                bot_id: None,
                protocol_version: None,
            })
            .await
            .map_err(|error| {
                ApplicationError::internal(format!("bot connect failed: {error}"))
            })?;
        if let Err(error) = self
            .bot_onboarding
            .admin_onboard_bot(AdminBotOnboardCommand {
                bot_uuid: connect.bot_uuid.clone(),
                name: Some(bot_name.to_string()),
                summary: None,
                domains: vec![],
                skills: vec![],
                scopes: vec![],
                binding_channels: None,
                actor_identity: Some(OnboardActorIdentity {
                    staff_no,
                    nick_name: None,
                }),
            })
            .await
        {
            tracing::warn!(
                bot_uuid = %connect.bot_uuid,
                error = %error,
                "register: admin_onboard_bot failed after connect"
            );
        }
        Ok(BotRegistration {
            bot_name: bot_name.to_string(),
            bot_uuid: connect.bot_uuid,
            bot_token: connect.token,
        })
    }
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    use bcs_service_api::ActorKind;

    struct FakeBotManagement {
        fail_connect: bool,
        connected: std::sync::Mutex<Vec<BotConnectCommand>>,
    }

    impl FakeBotManagement {
        fn new(fail_connect: bool) -> Self {
            Self {
                fail_connect,
                connected: std::sync::Mutex::new(Vec::new()),
            }
        }
    }

    #[async_trait]
    impl BotManagementService for FakeBotManagement {
        async fn connect_bot(
            &self,
            command: BotConnectCommand,
        ) -> Result<BotConnectResult, BotUseCaseError> {
            if self.fail_connect {
                return Err(BotUseCaseError::Unauthorized("connect refused".into()));
            }
            self.connected
                .lock()
                .expect("connect lock")
                .push(command);
            Ok(BotConnectResult {
                is_new: true,
                bot_uuid: "bot-1".to_string(),
                token: "bot-token-1".to_string(),
            })
        }

        async fn update_status(
            &self,
            _command: BotStatusUpdateCommand,
        ) -> Result<BotStatusUpdateResult, BotUseCaseError> {
            unreachable!("register flow never calls update_status")
        }

        async fn set_visibility(
            &self,
            _command: BotVisibilityCommand,
        ) -> Result<BotVisibilityResult, BotUseCaseError> {
            unreachable!("register flow never calls set_visibility")
        }

        async fn leave_bot(
            &self,
            _command: BotLeaveCommand,
        ) -> Result<BotLeaveResult, BotUseCaseError> {
            unreachable!("register flow never calls leave_bot")
        }

        async fn switch_delivery_to_provider(
            &self,
            _command: SwitchDeliveryToProviderCommand,
        ) -> Result<SwitchDeliveryToProviderResult, BotUseCaseError> {
            unreachable!("register flow never calls switch_delivery_to_provider")
        }
    }

    struct FakeBotOnboarding {
        fail: bool,
        onboarded: std::sync::Mutex<Vec<AdminBotOnboardCommand>>,
    }

    impl FakeBotOnboarding {
        fn new(fail: bool) -> Self {
            Self {
                fail,
                onboarded: std::sync::Mutex::new(Vec::new()),
            }
        }
    }

    #[async_trait]
    impl BotOnboardingService for FakeBotOnboarding {
        async fn onboard_bot(
            &self,
            _command: bcs_service_api::BotOnboardCommand,
        ) -> Result<bcs_service_api::BotOnboardResult, ServiceError> {
            unreachable!("register flow never calls onboard_bot")
        }

        async fn admin_onboard_bot(
            &self,
            command: AdminBotOnboardCommand,
        ) -> Result<bcs_service_api::BotOnboardResult, ServiceError> {
            if self.fail {
                return Err(ServiceError::BotNotRegistered("bot-1".into()));
            }
            let bot_uuid = command.bot_uuid.clone();
            let name = command.name.clone();
            self.onboarded
                .lock()
                .expect("onboard lock")
                .push(command);
            Ok(bcs_service_api::BotOnboardResult {
                bot_uuid,
                onboarded: true,
                name,
                message: None,
                binding_results: HashMap::new(),
                unbound: Vec::new(),
                capabilities: None,
                actor_kind: ActorKind::Bot,
            })
        }

        async fn ensure_bot(
            &self,
            _command: bcs_service_api::EnsureBotCommand,
        ) -> Result<bcs_service_api::EnsureBotResult, ServiceError> {
            unreachable!("register flow never calls ensure_bot")
        }
    }

    const SECRET: &[u8] = b"test-secret-key-32-bytes-long!!!";

    fn human_caller(id: &str) -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity {
                id: id.to_string(),
                username: id.to_string(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    fn service(
        fail_connect: bool,
        fail_onboard: bool,
    ) -> (RegisterServiceImpl, Arc<FakeBotManagement>, Arc<FakeBotOnboarding>) {
        let management = Arc::new(FakeBotManagement::new(fail_connect));
        let onboarding = Arc::new(FakeBotOnboarding::new(fail_onboard));
        (
            RegisterServiceImpl::new(
                management.clone() as Arc<dyn BotManagementService>,
                onboarding.clone() as Arc<dyn BotOnboardingService>,
                SECRET.to_vec(),
            ),
            management,
            onboarding,
        )
    }

    #[tokio::test]
    async fn issuance_round_trips_through_verification() {
        let (svc, _, _) = service(false, false);
        let view = svc
            .issue_register_token(IssueRegisterToken {
                caller: human_caller("staff-1"),
            })
            .await
            .expect("issue");
        assert_eq!(view.note, REGISTER_TOKEN_NOTE);
        let payload = register_token_decode_and_verify(&view.token, SECRET).expect("verify");
        assert_eq!(payload.id, "human_staff-1");
        assert_eq!(payload.v, 1);
        assert!(payload.exp >= now_secs() + REGISTER_TOKEN_TTL_SECONDS - 5);
        assert_eq!(view.expires_at, payload.exp * 1000);
    }

    #[tokio::test]
    async fn issuance_rejects_non_human_principals() {
        let (svc, _, _) = service(false, false);
        let error = svc
            .issue_register_token(IssueRegisterToken {
                caller: AuthenticatedCaller {
                    tenant: None,
                    user: None,
                    bot: None,
                    app: None,
                    access_key: None,
                },
            })
            .await
            .expect_err("bot-only principal is rejected");
        assert!(matches!(error, ApplicationError::Forbidden(_)));
    }

    #[tokio::test]
    async fn registration_connects_and_onboards_with_staff_no() {
        let (svc, management, onboarding) = service(false, false);
        let token_view = svc
            .issue_register_token(IssueRegisterToken {
                caller: human_caller("staff-9"),
            })
            .await
            .expect("issue");
        let registration = svc
            .register_bot(RegisterBot {
                token: token_view.token,
                bot_name: "  测试机器人  ".to_string(),
            })
            .await
            .expect("register");
        assert_eq!(registration.bot_name, "测试机器人");
        assert_eq!(registration.bot_uuid, "bot-1");
        assert_eq!(registration.bot_token, "bot-token-1");
        assert_eq!(
            onboarding
                .onboarded
                .lock()
                .expect("onboard lock")
                .last()
                .as_ref()
                .and_then(|cmd| cmd.actor_identity.as_ref())
                .map(|identity| identity.staff_no.as_str()),
            Some("staff-9")
        );
        assert_eq!(
            management.connected.lock().expect("connect lock").len(),
            1
        );
    }

    #[tokio::test]
    async fn token_verification_failures_map_to_unauthenticated() {
        let (svc, _, _) = service(false, false);
        let view = svc
            .issue_register_token(IssueRegisterToken {
                caller: human_caller("staff-1"),
            })
            .await
            .expect("issue");
        for bad in ["not-a-token"] {
            let error = svc
                .register_bot(RegisterBot {
                    token: bad.to_string(),
                    bot_name: "ok-name".to_string(),
                })
                .await
                .expect_err("malformed token is rejected");
            assert!(matches!(error, ApplicationError::Unauthenticated));
        }
        // Wrong-secret token (well-formed, different HMAC).
        let other = register_token_encode(
            &RegisterTokenPayload {
                v: 1,
                id: "human_staff-1".to_string(),
                exp: now_secs() + 60,
            },
            b"other-secret",
        );
        let error = svc
            .register_bot(RegisterBot {
                token: other,
                bot_name: "ok-name".to_string(),
            })
            .await
            .expect_err("foreign-secret token is rejected");
        assert!(matches!(error, ApplicationError::Unauthenticated));
    }

    #[tokio::test]
    async fn registration_rejects_invalid_name_lengths() {
        let (svc, _, _) = service(false, false);
        let too_long = "x".repeat(65);
        for bad in ["a", too_long.as_str()] {
            let error = svc
                .register_bot(RegisterBot {
                    token: "unused".to_string(),
                    bot_name: bad.to_string(),
                })
                .await
                .expect_err("bad name length is rejected");
            assert!(matches!(error, ApplicationError::InvalidInput { .. }));
        }
    }

    #[tokio::test]
    async fn connect_failure_maps_to_internal() {
        let (svc, _, _) = service(true, false);
        let token_view = svc
            .issue_register_token(IssueRegisterToken {
                caller: human_caller("staff-1"),
            })
            .await
            .expect("issue");
        let error = svc
            .register_bot(RegisterBot {
                token: token_view.token,
                bot_name: "ok-name".to_string(),
            })
            .await
            .expect_err("connect failure surfaces");
        assert!(matches!(error, ApplicationError::Internal(_)));
    }

    #[tokio::test]
    async fn onboard_failure_is_swallowed() {
        let (svc, _, onboarding) = service(false, true);
        let token_view = svc
            .issue_register_token(IssueRegisterToken {
                caller: human_caller("staff-1"),
            })
            .await
            .expect("issue");
        let registration = svc
            .register_bot(RegisterBot {
                token: token_view.token,
                bot_name: "ok-name".to_string(),
            })
            .await
            .expect("onboard failure must not fail registration");
        assert_eq!(registration.bot_uuid, "bot-1");
        assert!(onboarding.onboarded.lock().expect("onboard lock").is_empty());
    }
}