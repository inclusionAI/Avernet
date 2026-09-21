use super::*;
use bcs_domain::provider_registration_token::ProviderRegistrationMode;
use bcs_domain::provider_registration_token::{self, ProviderRegisterTokenPayload};
use bcs_service_api::core::provider_registration::{
    ProviderRegistrationCoreService, ProviderRegistrationRecord, ProviderRegistrationResult,
    RegisterProviderBot,
};
use serde_json::json;
use std::sync::Mutex;

#[derive(Default)]
pub(super) struct FakeProviderRegistration {
    pub authorized: Mutex<Vec<(String, String)>>,
    pub registered: Mutex<Vec<RegisterProviderBot>>,
    pub issue_error: Mutex<Option<ServiceError>>,
    pub register_error: Mutex<Option<ServiceError>>,
}

#[async_trait]
impl ProviderRegistrationCoreService for FakeProviderRegistration {
    async fn authorize(
        &self,
        provider: &str,
        owner: &str,
    ) -> Result<Vec<ProviderRegistrationMode>, ServiceError> {
        self.authorized
            .lock()
            .unwrap()
            .push((provider.into(), owner.into()));
        if let Some(error) = self.issue_error.lock().unwrap().take() {
            return Err(error);
        }
        Ok(vec![
            ProviderRegistrationMode::Upstream,
            ProviderRegistrationMode::Gateway,
        ])
    }

    async fn register(
        &self,
        command: RegisterProviderBot,
    ) -> Result<ProviderRegistrationResult, ServiceError> {
        self.registered.lock().unwrap().push(command.clone());
        if let Some(error) = self.register_error.lock().unwrap().take() {
            return Err(error);
        }
        Ok(ProviderRegistrationResult {
            effective_webhook_url: if command.mode == ProviderRegistrationMode::Gateway {
                Some(
                    command
                        .webhook_url
                        .clone()
                        .unwrap_or_else(|| "https://example.test/default".into()),
                )
            } else {
                None
            },
            record: ProviderRegistrationRecord {
                provider_id: command.provider_id,
                provider_bot_ref: command.provider_bot_ref,
                owner: command.owner,
                mode: command.mode,
                bot_name: command.bot_name,
                bot_uuid: "scoped-bot".into(),
                bot_token: "runtime-token".into(),
                webhook_url: command.webhook_url,
            },
        })
    }
}

pub(super) fn scoped_service() -> (
    RegisterServiceImpl,
    Arc<FakeProviderRegistration>,
    Arc<FakeBotManagement>,
    Arc<FakeBotOnboarding>,
) {
    let (svc, management, onboarding) = service(false, false);
    let core = Arc::new(FakeProviderRegistration::default());
    (
        svc.with_provider_registration(core.clone()),
        core,
        management,
        onboarding,
    )
}

pub(super) fn scoped_payload() -> ProviderRegisterTokenPayload {
    ProviderRegisterTokenPayload {
        v: 2,
        purpose: "provider_bot_registration".into(),
        id: "human_human_staff-1".into(),
        provider_id: "provider-a".into(),
        allowed_modes: vec![
            ProviderRegistrationMode::Upstream,
            ProviderRegistrationMode::Gateway,
        ],
        exp: now_secs() + 60,
    }
}

fn register_command(token: String) -> RegisterBot {
    RegisterBot {
        token,
        bot_name: "Test Bot".into(),
        mode: None,
        provider_bot_ref: None,
        webhook_url: None,
    }
}

#[tokio::test]
async fn legacy_token_cannot_request_provider_registration_before_bot_writes() {
    let (svc, management, onboarding) = service(false, false);
    let token = svc
        .issue_register_token(IssueRegisterToken {
            caller: human_caller("staff-1"),
            provider_id: None,
        })
        .await
        .unwrap()
        .token;
    for command in [
        RegisterBot {
            mode: Some(ProviderRegistrationMode::Gateway),
            ..register_command(token.clone())
        },
        RegisterBot {
            provider_bot_ref: Some("ref".into()),
            ..register_command(token.clone())
        },
        RegisterBot {
            webhook_url: Some("https://example.test/hook".into()),
            ..register_command(token.clone())
        },
        RegisterBot {
            provider_bot_ref: Some(String::new()),
            ..register_command(token.clone())
        },
        RegisterBot {
            webhook_url: Some(String::new()),
            ..register_command(token.clone())
        },
    ] {
        assert!(matches!(
            svc.register_bot(command).await,
            Err(ApplicationError::InvalidInput { .. })
        ));
    }
    assert!(management.connected.lock().unwrap().is_empty());
    assert!(onboarding.onboarded.lock().unwrap().is_empty());
}

#[tokio::test]
async fn provider_issuance_is_disabled_by_default() {
    let (svc, _, _) = service(false, false);
    assert!(matches!(
        svc.issue_register_token(IssueRegisterToken {
            caller: human_caller("staff-1"),
            provider_id: Some("provider-a".into()),
        })
        .await,
        Err(ApplicationError::InvalidInput { .. })
    ));
}

#[tokio::test]
async fn blank_provider_is_invalid_not_legacy_issuance() {
    let (svc, _, _) = service(false, false);
    for provider_id in ["", "  ", "\t"] {
        assert!(matches!(
            svc.issue_register_token(IssueRegisterToken {
                caller: human_caller("staff-1"),
                provider_id: Some(provider_id.into()),
            })
            .await,
            Err(ApplicationError::InvalidInput { .. })
        ));
    }
}

#[tokio::test]
async fn provider_issuance_authorizes_human_and_signs_six_hour_scope() {
    let (svc, core, _, _) = scoped_service();
    let view = svc
        .issue_register_token(IssueRegisterToken {
            caller: human_caller("human_staff-1"),
            provider_id: Some("provider-a".into()),
        })
        .await
        .unwrap();
    assert_eq!(
        *core.authorized.lock().unwrap(),
        vec![("provider-a".into(), "human_staff-1".into())]
    );
    let payload = provider_registration_token::decode_and_verify(&view.token, SECRET).unwrap();
    assert_eq!(payload.v, 2);
    assert_eq!(payload.purpose, "provider_bot_registration");
    assert_eq!(payload.id, "human_human_staff-1");
    assert_eq!(payload.provider_id, "provider-a");
    assert!(payload.exp >= now_secs() + REGISTER_TOKEN_TTL_SECONDS - 5);
    assert_eq!(view.expires_at, payload.exp * 1000);
    assert_eq!(
        serde_json::to_value(view.registration).unwrap(),
        json!({
            "token_version": 2, "provider_id": "provider-a", "allowed_modes": ["upstream", "gateway"],
        })
    );
    assert!(register_token_decode_and_verify(&view.token, SECRET).is_err());
}

#[tokio::test]
async fn provider_issuance_requires_human_before_core_authorization() {
    let (svc, core, _, _) = scoped_service();
    let mut caller = human_caller("unused");
    caller.user = None;
    assert!(matches!(
        svc.issue_register_token(IssueRegisterToken {
            caller,
            provider_id: Some("provider-a".into()),
        })
        .await,
        Err(ApplicationError::Forbidden(_))
    ));
    assert!(core.authorized.lock().unwrap().is_empty());
}

#[tokio::test]
async fn scoped_registration_uses_token_identity_and_maps_only_public_result() {
    let (svc, core, management, onboarding) = scoped_service();
    for mode in [
        None,
        Some(ProviderRegistrationMode::Upstream),
        Some(ProviderRegistrationMode::Gateway),
    ] {
        let view = svc
            .register_bot(RegisterBot {
                mode,
                provider_bot_ref: Some("ref-1".into()),
                ..register_command(provider_registration_token::encode(
                    &scoped_payload(),
                    SECRET,
                ))
            })
            .await
            .unwrap();
        assert_eq!(view.bot_uuid, "scoped-bot");
        assert_eq!(view.bot_token, "runtime-token");
        let value = serde_json::to_value(view).unwrap();
        assert_eq!(value.as_object().unwrap().len(), 4);
        assert_eq!(value["registration"]["provider_id"], "provider-a");
        assert_eq!(value["registration"]["provider_bot_ref"], "ref-1");
        assert_eq!(value["registration"]["webhook_url"], json!(null));
        assert_eq!(
            value["registration"]["effective_webhook_url"],
            if mode == Some(ProviderRegistrationMode::Gateway) {
                json!("https://example.test/default")
            } else {
                json!(null)
            }
        );
        assert_eq!(value["registration"].as_object().unwrap().len(), 5);
        assert!(value["registration"].get("owner").is_none());
        assert!(value["registration"].get("bot_token").is_none());
        assert!(value["registration"].get("completed").is_none());
        let calls = core.registered.lock().unwrap();
        let command = calls.last().unwrap();
        assert_eq!(command.provider_id, "provider-a");
        assert_eq!(command.owner, "human_staff-1");
        assert_eq!(
            command.mode,
            mode.unwrap_or(ProviderRegistrationMode::Upstream)
        );
    }
    assert!(management.connected.lock().unwrap().is_empty());
    assert!(onboarding.onboarded.lock().unwrap().is_empty());
}

#[tokio::test]
async fn scoped_registration_requires_nonblank_reference_before_core() {
    let (svc, core, management, _) = scoped_service();
    for reference in [None, Some(""), Some("  ")] {
        assert!(matches!(
            svc.register_bot(RegisterBot {
                provider_bot_ref: reference.map(str::to_string),
                ..register_command(provider_registration_token::encode(
                    &scoped_payload(),
                    SECRET
                ))
            })
            .await,
            Err(ApplicationError::InvalidInput { .. })
        ));
    }
    assert!(core.registered.lock().unwrap().is_empty());
    assert!(management.connected.lock().unwrap().is_empty());
}

#[tokio::test]
async fn requested_mode_must_be_in_signed_allowlist() {
    let (svc, core, _, _) = scoped_service();
    for (allowed, requested) in [
        (
            ProviderRegistrationMode::Upstream,
            Some(ProviderRegistrationMode::Gateway),
        ),
        (ProviderRegistrationMode::Gateway, None),
    ] {
        let payload = ProviderRegisterTokenPayload {
            allowed_modes: vec![allowed],
            ..scoped_payload()
        };
        assert!(matches!(
            svc.register_bot(RegisterBot {
                mode: requested,
                provider_bot_ref: Some("ref-1".into()),
                ..register_command(provider_registration_token::encode(&payload, SECRET))
            })
            .await,
            Err(ApplicationError::Forbidden(_))
        ));
    }
    assert!(core.registered.lock().unwrap().is_empty());
}

#[tokio::test]
async fn expired_or_wrong_secret_tokens_never_reach_core() {
    let (svc, core, management, _) = scoped_service();
    let expired = ProviderRegisterTokenPayload {
        exp: 0,
        ..scoped_payload()
    };
    for token in [
        provider_registration_token::encode(&expired, SECRET),
        provider_registration_token::encode(&scoped_payload(), b"wrong-secret"),
        register_token_encode(
            &RegisterTokenPayload {
                v: 1,
                id: "human_staff-1".into(),
                exp: 0,
            },
            SECRET,
        ),
        register_token_encode(
            &RegisterTokenPayload {
                v: 3,
                id: "human_staff-1".into(),
                exp: now_secs() + 60,
            },
            SECRET,
        ),
        "not-a-token".into(),
    ] {
        assert!(matches!(
            svc.register_bot(RegisterBot {
                provider_bot_ref: Some("ref-1".into()),
                ..register_command(token)
            })
            .await,
            Err(ApplicationError::Unauthenticated)
        ));
    }
    assert!(core.registered.lock().unwrap().is_empty());
    assert!(management.connected.lock().unwrap().is_empty());
}

pub(super) fn core_errors() -> Vec<(ServiceError, &'static str)> {
    vec![
        (ServiceError::Forbidden("secret-policy".into()), "forbidden"),
        (
            ServiceError::ProviderNotFound("secret-provider".into()),
            "provider_not_found",
        ),
        (
            ServiceError::Conflict("secret-existing-token".into()),
            "registration_conflict",
        ),
        (
            ServiceError::InvalidOperation {
                message: "secret-input".into(),
                request_id: None,
            },
            "invalid_request",
        ),
        (
            ServiceError::ProviderNotReadyForDownlink {
                provider_id: "secret-provider".into(),
                reason: "secret-credential".into(),
            },
            "invalid_request",
        ),
        (
            ServiceError::InternalError("secret-database".into()),
            "internal_error",
        ),
        (
            ServiceError::IoError(std::io::Error::other("secret-path")),
            "internal_error",
        ),
    ]
}

#[tokio::test]
async fn issuance_and_redemption_core_errors_are_typed_and_redacted() {
    for redeem in [false, true] {
        for (error, expected_code) in core_errors() {
            let (svc, core, management, onboarding) = scoped_service();
            let actual = if redeem {
                *core.register_error.lock().unwrap() = Some(error);
                svc.register_bot(RegisterBot {
                    provider_bot_ref: Some("ref-1".into()),
                    ..register_command(provider_registration_token::encode(
                        &scoped_payload(),
                        SECRET,
                    ))
                })
                .await
                .unwrap_err()
            } else {
                *core.issue_error.lock().unwrap() = Some(error);
                svc.issue_register_token(IssueRegisterToken {
                    caller: human_caller("staff-1"),
                    provider_id: Some("provider-a".into()),
                })
                .await
                .unwrap_err()
            };
            assert_eq!(actual.code(), expected_code);
            assert!(!actual.to_string().contains("secret"));
            assert!(management.connected.lock().unwrap().is_empty());
            assert!(onboarding.onboarded.lock().unwrap().is_empty());
        }
    }
}

#[tokio::test]
async fn legacy_token_and_registration_json_keep_exact_old_shape() {
    let (svc, core, _, _) = scoped_service();
    let view = svc
        .issue_register_token(IssueRegisterToken {
            caller: human_caller("staff-1"),
            provider_id: None,
        })
        .await
        .unwrap();
    let value = serde_json::to_value(&view).unwrap();
    assert_eq!(
        value,
        json!({
            "token": view.token, "expires_at": view.expires_at, "note": REGISTER_TOKEN_NOTE,
        })
    );
    let result = svc
        .register_bot(RegisterBot {
            mode: Some(ProviderRegistrationMode::Upstream),
            ..register_command(view.token)
        })
        .await
        .unwrap();
    assert_eq!(
        serde_json::to_value(result).unwrap(),
        json!({
            "bot_name": "Test Bot", "bot_uuid": "bot-1", "bot_token": "bot-token-1",
        })
    );
    assert!(core.authorized.lock().unwrap().is_empty());
    assert!(core.registered.lock().unwrap().is_empty());
}
