use bcs_cache_api::{CachePlugin, CacheSetMode};
use bcs_cache_local::InMemoryCachePlugin;
use bcs_coordination_store::CoordinationCacheStore;
use bcs_service_api::port::{CoordinationClaim, CoordinationContext, CoordinationIntentPort,
    CoordinationResult, CoordinationStatus};
use bcs_test_support::contract::port::coordination_intent::coordination_intent_port_contract_tests;
use serde_json::json;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
const ID: &str = "bcs_intent_0123456789abcdef0123456789abcdef";
fn now_ms() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64 }
fn context() -> CoordinationContext {
    CoordinationContext { bot_id: "manager".into(), group_id: "group".into(), session_id: None,
        run_id: "run".into(), tool_call_id: "tool".into() }
}
async fn fixture() -> (CoordinationCacheStore, Arc<InMemoryCachePlugin>) {
    let cache = Arc::new(InMemoryCachePlugin::new());
    // The Python producer writes this UTF-8 JSON directly with SET NX EX 86400.
    let payload = json!({"intent_id": ID, "v": 2, "tool": "bcs_assign_task",
        "arguments": {"target_bot": "worker", "message": "中".repeat(4198) + "\n\"🙂"},
        "created_at_ms": now_ms(), "expires_at_ms": now_ms() + 86400000});
    cache.set_value(&format!("bcs:coordination:v2:{ID}:payload"), serde_json::to_vec(&payload).unwrap(),
        Some(Duration::from_secs(86400)), CacheSetMode::InsertOnly).await.unwrap();
    (CoordinationCacheStore::new(cache.clone()), cache)
}
#[tokio::test]
async fn shared_cache_contract_and_immutable_receipts() {
    let (store, cache) = fixture().await;
    coordination_intent_port_contract_tests(&store, ID, "bcs_assign_task", &context(), now_ms()+10000).await;
    let other = CoordinationCacheStore::new(cache.clone());
    assert!(matches!(other.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await.unwrap(), CoordinationClaim::Duplicate(Some(_))));
    let claim: serde_json::Value = serde_json::from_slice(&cache.get_value(&format!("bcs:coordination:v2:{ID}:claim")).await.unwrap().unwrap()).unwrap();
    let token = claim["claim_token"].as_str().unwrap();
    let result = CoordinationResult { status: CoordinationStatus::Applied, task_id: Some("task-real".into()), error_code: None };
    other.finish(ID, &context(), token, &result).await.unwrap();
    let conflicting = CoordinationResult { status: CoordinationStatus::Failed, ..result.clone() };
    assert!(other.finish(ID, &context(), token, &conflicting).await.is_err());
    assert!(other.finish(ID, &context(), "wrong", &result).await.is_err());
    cache.delete(&format!("bcs:coordination:v2:{ID}:payload")).await.unwrap();
    other.finish(ID, &context(), token, &result).await.unwrap();
    assert!(other.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await.is_err());
}
#[tokio::test]
async fn concurrent_instances_have_one_winner_and_no_reclaim_after_receipt_loss() {
    let (_, cache) = fixture().await;
    let mut tasks = Vec::new();
    for _ in 0..32 {
        let store = CoordinationCacheStore::new(cache.clone());
        tasks.push(tokio::spawn(async move {
            store.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await.unwrap()
        }));
    }
    let mut winners = 0;
    for task in tasks { if matches!(task.await.unwrap(), CoordinationClaim::Acquired(_)) { winners += 1; } }
    assert_eq!(winners, 1);
    let store = CoordinationCacheStore::new(cache);
    assert!(matches!(store.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await.unwrap(), CoordinationClaim::Duplicate(None)));
    let mut wrong = context(); wrong.run_id = "another-run".into();
    assert!(store.resolve_and_claim(ID, "bcs_assign_task", &wrong, now_ms()+10000).await.is_err());
}
#[tokio::test]
async fn mismatch_and_expired_run_do_not_claim() {
    let (store, cache) = fixture().await;
    assert!(store.resolve_and_claim(ID, "bcs_task_complete", &context(), now_ms()+10000).await.is_err());
    assert!(store.resolve_and_claim(ID, "bcs_assign_task", &context(), 1).await.is_err());
    assert!(cache.get_value(&format!("bcs:coordination:v2:{ID}:claim")).await.unwrap().is_none());
}

struct FaultCache {
    inner: Arc<InMemoryCachePlugin>,
    lose_claim_ack: bool,
    claim_writes: std::sync::atomic::AtomicUsize,
    receipt_writes: std::sync::atomic::AtomicUsize,
    reads: std::sync::atomic::AtomicUsize,
}
#[async_trait::async_trait]
impl CachePlugin for FaultCache {
    async fn get_value(&self, key: &str) -> bcs_cache_api::CacheResult<Option<Vec<u8>>> {
        if self.reads.fetch_add(1, std::sync::atomic::Ordering::SeqCst) == 0 {
            return Err(bcs_cache_api::CacheError::Backend("temporary read failure".into()));
        }
        self.inner.get_value(key).await
    }
    async fn set_value(&self, key: &str, value: Vec<u8>, ttl: Option<Duration>, mode: CacheSetMode) -> bcs_cache_api::CacheResult<bool> {
        let applied = self.inner.set_value(key, value, ttl, mode).await?;
        let lose_ack = if key.ends_with(":claim") {
            self.claim_writes.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            self.lose_claim_ack
        } else {
            self.receipt_writes.fetch_add(1, std::sync::atomic::Ordering::SeqCst) == 0
        };
        if lose_ack && applied { return Err(bcs_cache_api::CacheError::Backend("write committed; acknowledgement lost".into())); }
        Ok(applied)
    }
    async fn delete(&self, key: &str) -> bcs_cache_api::CacheResult<bool> { self.inner.delete(key).await }
    async fn expire(&self, key: &str, ttl: Duration) -> bcs_cache_api::CacheResult<bool> { self.inner.expire(key, ttl).await }
    async fn ttl(&self, key: &str) -> bcs_cache_api::CacheResult<bcs_cache_api::CacheTtl> { self.inner.ttl(key).await }
    async fn hash_get(&self, key: &str, field: &str) -> bcs_cache_api::CacheResult<Option<Vec<u8>>> { self.inner.hash_get(key, field).await }
    async fn hash_get_all(&self, key: &str) -> bcs_cache_api::CacheResult<std::collections::BTreeMap<String, Vec<u8>>> { self.inner.hash_get_all(key).await }
    async fn hash_set(&self, key: &str, field: &str, value: Vec<u8>) -> bcs_cache_api::CacheResult<()> { self.inner.hash_set(key, field, value).await }
    async fn hash_set_many(&self, key: &str, fields: std::collections::BTreeMap<String, Vec<u8>>) -> bcs_cache_api::CacheResult<()> { self.inner.hash_set_many(key, fields).await }
    async fn hash_delete(&self, key: &str, field: &str) -> bcs_cache_api::CacheResult<bool> { self.inner.hash_delete(key, field).await }
}
#[tokio::test]
async fn uncertain_claim_does_not_execute_and_finish_retries_only_receipt() {
    for lose_claim_ack in [true, false] {
        let (_, inner) = fixture().await;
        let cache = Arc::new(FaultCache { inner, lose_claim_ack, claim_writes: 0.into(), receipt_writes: 0.into(), reads: 0.into() });
        let store = CoordinationCacheStore::new(cache.clone());
        let claim = store.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await;
        if lose_claim_ack {
            assert!(claim.is_err());
            assert_eq!(cache.claim_writes.load(std::sync::atomic::Ordering::SeqCst), 1);
            assert!(matches!(store.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await.unwrap(), CoordinationClaim::Duplicate(None)));
        } else {
            let CoordinationClaim::Acquired(lease) = claim.unwrap() else { panic!("expected claim") };
            let result = CoordinationResult { status: CoordinationStatus::Applied, task_id: Some("task-real".into()), error_code: None };
            store.finish(ID, &context(), &lease.claim_token, &result).await.unwrap();
            assert_eq!(cache.claim_writes.load(std::sync::atomic::Ordering::SeqCst), 1);
            assert_eq!(cache.receipt_writes.load(std::sync::atomic::Ordering::SeqCst), 2);
        }
    }
}
