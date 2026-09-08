use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, BindInviteCode, BindInviteCodeResult,
    ClaimPublicInviteCode, ClaimPublicInviteCodeResult, GetMyInviteCodeBinding, InitInviteCodes,
    InitInviteCodesResult, InviteCodeBindingView, InviteCodeService,
};
use bcs_service_api::port::repo::{
    InviteCodeBindOutcome, InviteCodeRecord, InviteCodeRepoPort, InviteCodeStatus,
};
use hmac::{Hmac, Mac};
use rand::rngs::OsRng;
use rand::RngCore;
use sha2::Sha256;
use tracing::warn;

type HmacSha256 = Hmac<Sha256>;

const INVITE_CODE_LEN: usize = 6;
const INVITE_CODE_CHARSET: &[u8] = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const PUBLIC_OPENAPI_CREATOR: &str = "public_openapi";

pub struct InviteCodeServiceImpl {
    repo: Arc<dyn InviteCodeRepoPort>,
    secret: Vec<u8>,
    public_claim_max_count: u64,
    public_claim_lock: tokio::sync::Mutex<()>,
}

impl InviteCodeServiceImpl {
    pub fn new(
        repo: Arc<dyn InviteCodeRepoPort>,
        secret: Vec<u8>,
        public_claim_max_count: u64,
    ) -> Self {
        Self {
            repo,
            secret,
            public_claim_max_count,
            public_claim_lock: tokio::sync::Mutex::new(()),
        }
    }

    fn normalize_code(code: &str) -> Result<String, ApplicationError> {
        let code = code.trim().to_uppercase();
        if code.len() != INVITE_CODE_LEN {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "invite code must be exactly 6 characters",
            ));
        }
        if !code
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte.is_ascii_uppercase())
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "invite code must contain only digits and uppercase letters",
            ));
        }
        Ok(code)
    }

    fn code_hash(&self, code: &str) -> String {
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .expect("invite-code HMAC secret is not empty");
        mac.update(code.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    fn code_hint(code: &str) -> String {
        code.chars().rev().take(4).collect::<String>().chars().rev().collect()
    }

    fn generate_code() -> String {
        let mut rng = OsRng;
        let mut code = String::with_capacity(INVITE_CODE_LEN);
        for _ in 0..INVITE_CODE_LEN {
            let idx = (rng.next_u32() as usize) % INVITE_CODE_CHARSET.len();
            code.push(INVITE_CODE_CHARSET[idx] as char);
        }
        code
    }

    fn now_secs() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    }

    fn record_for_code(&self, code: &str, created_by: Option<String>) -> InviteCodeRecord {
        let now = Self::now_secs();
        InviteCodeRecord {
            id: 0,
            code_hash: self.code_hash(code),
            code_hint: Self::code_hint(code),
            status: InviteCodeStatus::Active,
            bound_user_id: None,
            bound_at: None,
            created_by,
            created_at: now,
            updated_at: now,
        }
    }

    async fn generate_and_store_code(
        &self,
        created_by: Option<String>,
    ) -> Result<String, ApplicationError> {
        loop {
            let code = Self::generate_code();
            let record = self.record_for_code(&code, created_by.clone());
            match self.repo.insert_code(record).await {
                Ok(true) => return Ok(code),
                Ok(false) => continue,
                Err(error) => {
                    warn!(error = %error, "invite code generation failed");
                    return Err(ApplicationError::internal(format!(
                        "failed to generate invite code: {error}"
                    )));
                }
            }
        }
    }

    fn human_user_id(caller: &AuthenticatedCaller) -> Result<&str, ApplicationError> {
        caller.user.as_ref().map(|user| user.id.as_str()).ok_or_else(|| {
            ApplicationError::invite_code_not_applicable(
                "invite codes are only available to human callers",
            )
        })
    }
}

#[async_trait]
impl InviteCodeService for InviteCodeServiceImpl {
    async fn init_invite_codes(
        &self,
        command: InitInviteCodes,
    ) -> Result<InitInviteCodesResult, ApplicationError> {
        let mut codes = Vec::with_capacity(command.count as usize);
        while codes.len() < command.count as usize {
            codes.push(self.generate_and_store_code(None).await?);
        }
        Ok(InitInviteCodesResult { codes })
    }

    async fn claim_public_invite_code(
        &self,
        _command: ClaimPublicInviteCode,
    ) -> Result<ClaimPublicInviteCodeResult, ApplicationError> {
        let _claim_guard = self.public_claim_lock.lock().await;
        let claimed_count = self
            .repo
            .count_by_created_by(PUBLIC_OPENAPI_CREATOR)
            .await
            .map_err(|error| ApplicationError::internal(format!(
                "failed to count publicly claimed invite codes: {error}"
            )))?;
        if claimed_count >= self.public_claim_max_count {
            return Err(ApplicationError::invite_code_claim_limit_reached(
                "maximum public invite-code claim count has been reached",
            ));
        }

        let invite_code = self
            .generate_and_store_code(Some(PUBLIC_OPENAPI_CREATOR.to_string()))
            .await?;
        Ok(ClaimPublicInviteCodeResult { invite_code })
    }

    async fn bind_invite_code(
        &self,
        command: BindInviteCode,
    ) -> Result<BindInviteCodeResult, ApplicationError> {
        let user_id = Self::human_user_id(&command.caller)?;
        let code = Self::normalize_code(&command.code)?;
        let bound_at = Self::now_secs();

        if let Some(existing) = self
            .repo
            .find_by_user_id(user_id)
            .await
            .map_err(|error| ApplicationError::internal(format!(
                "failed to load invite-code binding: {error}"
            )))?
        {
            if existing.code_hash == self.code_hash(&code) {
                return Ok(BindInviteCodeResult {
                    bound: true,
                    bound_at: existing.bound_at.unwrap_or(bound_at),
                });
            }
            return Err(ApplicationError::invite_code_already_bound(
                "this human caller has already bound a different invite code",
            ));
        }

        let outcome = self
            .repo
            .bind_code(&self.code_hash(&code), user_id, bound_at)
            .await
            .map_err(|error| ApplicationError::internal(format!(
                "failed to bind invite-code: {error}"
            )))?;

        match outcome {
            InviteCodeBindOutcome::Bound(record)
            | InviteCodeBindOutcome::AlreadyBoundToSameCode(record) => Ok(BindInviteCodeResult {
                bound: true,
                bound_at: record.bound_at.unwrap_or(bound_at),
            }),
            InviteCodeBindOutcome::AlreadyBoundToDifferentCode(_) => Err(
                ApplicationError::invite_code_already_bound(
                    "this human caller has already bound a different invite code",
                ),
            ),
            InviteCodeBindOutcome::Unavailable => Err(ApplicationError::invite_code_unavailable(
                "invite code is unavailable",
            )),
        }
    }

    async fn get_my_invite_code_binding(
        &self,
        command: GetMyInviteCodeBinding,
    ) -> Result<InviteCodeBindingView, ApplicationError> {
        let user_id = Self::human_user_id(&command.caller)?;
        let record = self
            .repo
            .find_by_user_id(user_id)
            .await
            .map_err(|error| ApplicationError::internal(format!(
                "failed to load invite-code binding: {error}"
            )))?;
        Ok(match record {
            Some(record) => InviteCodeBindingView {
                bound: true,
                bound_at: record.bound_at,
            },
            None => InviteCodeBindingView {
                bound: false,
                bound_at: None,
            },
        })
    }

    async fn ensure_invite_code_access(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<(), ApplicationError> {
        let Some(user) = caller.user.as_ref() else {
            return Ok(());
        };
        if self
            .repo
            .find_by_user_id(&user.id)
            .await
            .map_err(|error| ApplicationError::internal(format!(
                "failed to verify invite-code access: {error}"
            )))?
            .is_some()
        {
            Ok(())
        } else {
            Err(ApplicationError::invite_code_required(
                "human callers must bind an invite code before using platform features",
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    use bcs_service_api::application::v1::AuthenticatedUserIdentity;

    struct FakeInviteCodeRepo {
        records: tokio::sync::RwLock<HashMap<String, InviteCodeRecord>>,
    }

    impl FakeInviteCodeRepo {
        fn new() -> Self {
            Self {
                records: tokio::sync::RwLock::new(HashMap::new()),
            }
        }
    }

    #[async_trait]
    impl InviteCodeRepoPort for FakeInviteCodeRepo {
        async fn insert_code(&self, record: InviteCodeRecord) -> bcs_service_api::ServiceResult<bool> {
            let mut records = self.records.write().await;
            if records.contains_key(&record.code_hash) {
                return Ok(false);
            }
            records.insert(record.code_hash.clone(), record);
            Ok(true)
        }

        async fn count_by_created_by(&self, created_by: &str) -> bcs_service_api::ServiceResult<u64> {
            Ok(self
                .records
                .read()
                .await
                .values()
                .filter(|record| record.created_by.as_deref() == Some(created_by))
                .count() as u64)
        }

        async fn bind_code(
            &self,
            code_hash: &str,
            user_id: &str,
            bound_at: u64,
        ) -> bcs_service_api::ServiceResult<InviteCodeBindOutcome> {
            let mut records = self.records.write().await;
            let Some(record) = records.get_mut(code_hash) else {
                return Ok(InviteCodeBindOutcome::Unavailable);
            };
            if record.status == InviteCodeStatus::Disabled {
                return Ok(InviteCodeBindOutcome::Unavailable);
            }
            if record.bound_user_id.as_deref() == Some(user_id) {
                return Ok(InviteCodeBindOutcome::AlreadyBoundToSameCode(record.clone()));
            }
            if let Some(bound_user_id) = record.bound_user_id.as_ref() {
                let mut cloned = record.clone();
                cloned.bound_user_id = Some(bound_user_id.clone());
                return Ok(InviteCodeBindOutcome::AlreadyBoundToDifferentCode(cloned));
            }
            record.bound_user_id = Some(user_id.to_string());
            record.bound_at = Some(bound_at);
            record.status = InviteCodeStatus::Bound;
            record.updated_at = bound_at;
            Ok(InviteCodeBindOutcome::Bound(record.clone()))
        }

        async fn find_by_user_id(
            &self,
            user_id: &str,
        ) -> bcs_service_api::ServiceResult<Option<InviteCodeRecord>> {
            Ok(self
                .records
                .read()
                .await
                .values()
                .find(|record| record.bound_user_id.as_deref() == Some(user_id))
                .cloned())
        }

        async fn find_by_code_hash(
            &self,
            code_hash: &str,
        ) -> bcs_service_api::ServiceResult<Option<InviteCodeRecord>> {
            Ok(self.records.read().await.get(code_hash).cloned())
        }
    }

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

    fn service() -> (InviteCodeServiceImpl, Arc<FakeInviteCodeRepo>) {
        let repo = Arc::new(FakeInviteCodeRepo::new());
        (
            InviteCodeServiceImpl::new(
                repo.clone() as Arc<dyn InviteCodeRepoPort>,
                b"secret".to_vec(),
                1_000,
            ),
            repo,
        )
    }

    #[tokio::test]
    async fn init_codes_generates_requested_count() {
        let (svc, repo) = service();
        let result = svc
            .init_invite_codes(InitInviteCodes { count: 3 })
            .await
            .expect("init codes");
        assert_eq!(result.codes.len(), 3);
        assert!(result.codes.iter().all(|code| code.len() == 6));
        assert_eq!(repo.records.read().await.len(), 3);
    }

    #[tokio::test]
    async fn public_claim_generates_and_persists_one_code() {
        let (svc, repo) = service();
        let result = svc
            .claim_public_invite_code(ClaimPublicInviteCode)
            .await
            .expect("claim public invite code");

        assert_eq!(result.invite_code.len(), 6);
        assert!(result
            .invite_code
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte.is_ascii_uppercase()));
        let record = repo
            .records
            .read()
            .await
            .get(&svc.code_hash(&result.invite_code))
            .cloned()
            .expect("claimed code record");
        assert_eq!(record.status, InviteCodeStatus::Active);
        assert_eq!(record.created_by.as_deref(), Some("public_openapi"));
        assert!(record.bound_user_id.is_none());
    }

    #[tokio::test]
    async fn public_claim_rejects_when_max_count_is_reached() {
        let repo = Arc::new(FakeInviteCodeRepo::new());
        let svc = InviteCodeServiceImpl::new(
            repo.clone() as Arc<dyn InviteCodeRepoPort>,
            b"secret".to_vec(),
            1,
        );
        svc.claim_public_invite_code(ClaimPublicInviteCode)
            .await
            .expect("first public claim");

        let error = svc
            .claim_public_invite_code(ClaimPublicInviteCode)
            .await
            .expect_err("second public claim should exceed the limit");

        assert_eq!(error.code(), "invite_code_claim_limit_reached");
        assert_eq!(repo.records.read().await.len(), 1);
    }

    #[tokio::test]
    async fn concurrent_public_claims_do_not_exceed_max_count() {
        let repo = Arc::new(FakeInviteCodeRepo::new());
        let svc = InviteCodeServiceImpl::new(
            repo.clone() as Arc<dyn InviteCodeRepoPort>,
            b"secret".to_vec(),
            1,
        );

        let (first, second) = tokio::join!(
            svc.claim_public_invite_code(ClaimPublicInviteCode),
            svc.claim_public_invite_code(ClaimPublicInviteCode),
        );

        assert_eq!(u8::from(first.is_ok()) + u8::from(second.is_ok()), 1);
        assert_eq!(repo.records.read().await.len(), 1);
    }

    #[tokio::test]
    async fn bind_and_query_round_trip() {
        let (svc, repo) = service();
        let code = "A1B2C3".to_string();
        let hash = svc.code_hash(&code);
        repo.insert_code(svc.record_for_code(&code, None))
            .await
            .expect("insert code");
        let result = svc
            .bind_invite_code(BindInviteCode {
                caller: human_caller("user-1"),
                code: code.clone(),
            })
            .await
            .expect("bind code");
        assert!(result.bound);
        assert!(result.bound_at > 0);
        assert_eq!(
            repo.records.read().await.get(&hash).cloned().and_then(|record| record.bound_user_id),
            Some("user-1".to_string())
        );
        let view = svc
            .get_my_invite_code_binding(GetMyInviteCodeBinding {
                caller: human_caller("user-1"),
            })
            .await
            .expect("query binding");
        assert!(view.bound);
        assert!(view.bound_at.is_some());
    }

    #[tokio::test]
    async fn ensure_access_allows_bots_and_bound_humans() {
        let (svc, repo) = service();
        let code = "A1B2C3".to_string();
        let hash = svc.code_hash(&code);
        repo.insert_code(svc.record_for_code(&code, None))
            .await
            .expect("insert code");
        svc.bind_invite_code(BindInviteCode {
            caller: human_caller("user-1"),
            code,
        })
        .await
        .expect("bind code");
        assert!(svc.ensure_invite_code_access(&human_caller("user-1")).await.is_ok());
        assert!(svc.ensure_invite_code_access(&AuthenticatedCaller {
            tenant: None,
            user: None,
            bot: None,
            app: None,
            access_key: None,
        }).await.is_ok());
        assert_eq!(
            repo.records.read().await.get(&hash).cloned().and_then(|record| record.bound_user_id),
            Some("user-1".to_string())
        );
    }
}
