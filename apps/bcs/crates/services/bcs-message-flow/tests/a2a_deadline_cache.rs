use std::{collections::BTreeMap,sync::{Arc,Mutex},time::Duration};
use async_trait::async_trait;
use bcs_cache_api::{CacheError,CachePlugin,CacheResult,CacheSetMode,CacheTtl};
use bcs_cache_local::InMemoryCachePlugin;
use bcs_message_flow::RedisBotRunContextStore;
use bcs_service_api::{ActiveBotRunContext,BotRunContext,BotRunContextPort,BotRunScope,BotRunTransportOwner,ProviderRunTransport};

#[derive(Default)]
struct Cache {
    inner:InMemoryCachePlugin,
    fail:Mutex<Option<&'static str>>,
    trace:Mutex<Vec<String>>,
    pause_context:std::sync::atomic::AtomicBool,
    entered:tokio::sync::Notify,
    resume:tokio::sync::Notify,
}
impl Cache {
    fn record(&self,entry:String) {self.trace.lock().unwrap().push(entry);}
    fn fails(&self,stage:&str)->bool {*self.fail.lock().unwrap()==Some(stage)}
}
#[async_trait]
impl CachePlugin for Cache {
    async fn get_value(&self,key:&str)->CacheResult<Option<Vec<u8>>> {self.record("get".into());self.inner.get_value(key).await}
    async fn get_values(&self,keys:&[String])->CacheResult<Vec<Option<Vec<u8>>>> {
        self.record(format!("batch:{}",keys.len()));
        if self.fails("read") {return Err(CacheError::Backend("test read failure".into()));}
        self.inner.get_values(keys).await
    }
    async fn set_value(&self,key:&str,value:Vec<u8>,ttl:Option<Duration>,mode:CacheSetMode)->CacheResult<bool> {
        self.record(format!("set:{mode:?}"));
        if mode==CacheSetMode::UpdateOnly && key=="deadline:botrun:run" {
            if self.pause_context.load(std::sync::atomic::Ordering::SeqCst) {
                self.entered.notify_one();self.resume.notified().await;
            }
            if self.fails("write") {return Err(CacheError::Backend("test context write failure".into()));}
            if self.fails("expire") {self.inner.delete(key).await?;}
        }
        self.inner.set_value(key,value,ttl,mode).await
    }
    async fn delete(&self,key:&str)->CacheResult<bool> {self.record("delete".into());self.inner.delete(key).await}
    async fn expire(&self,key:&str,ttl:Duration)->CacheResult<bool> {
        self.record("expire".into());
        if self.fails("scope_expire") && key.starts_with("deadline:botrun:active-scope:") {return Ok(false);}
        self.inner.expire(key,ttl).await
    }
    async fn ttl(&self,key:&str)->CacheResult<CacheTtl> {self.record("ttl".into());self.inner.ttl(key).await}
    async fn hash_get(&self,key:&str,field:&str)->CacheResult<Option<Vec<u8>>> {self.record("hash_get".into());self.inner.hash_get(key,field).await}
    async fn hash_get_all(&self,key:&str)->CacheResult<BTreeMap<String,Vec<u8>>> {self.record("hash_get_all".into());self.inner.hash_get_all(key).await}
    async fn hash_set(&self,key:&str,field:&str,value:Vec<u8>)->CacheResult<()> {
        self.record("hash_set".into());
        if self.fails("scope") {return Err(CacheError::Backend("test scope failure".into()));}
        self.inner.hash_set(key,field,value).await
    }
    async fn hash_set_many(&self,key:&str,fields:BTreeMap<String,Vec<u8>>)->CacheResult<()> {self.record("hash_set_many".into());self.inner.hash_set_many(key,fields).await}
    async fn hash_delete(&self,key:&str,field:&str)->CacheResult<bool> {self.record("hash_delete".into());self.inner.hash_delete(key,field).await}
}
fn now()->u64 {std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64}
async fn fixture()->(Arc<Cache>,RedisBotRunContextStore,BotRunScope,u64,u64) {
    let cache=Arc::new(Cache::default());let store=RedisBotRunContextStore::new(cache.clone(),"deadline:".into(),500);
    let old=now()+30_000;let next=old+300_000;
    let scope=BotRunScope {bot_id:"bot".into(),group_id:"group".into(),session_id:"session".into()};
    store.put_context(BotRunContext {run_id:"run".into(),bot_id:scope.bot_id.clone(),group_id:scope.group_id.clone(),bcs_session_id:Some(scope.session_id.clone()),deadline_ms:old,terminal:false}).await;
    store.register_active_run(ActiveBotRunContext {canonical_run_id:"run".into(),downstream_run_id:"downstream".into(),downstream_session_key:Some("wire-session".into()),
        scope:scope.clone(),transport_owner:BotRunTransportOwner::A2a {provider_id:"provider".into(),provider_bot_ref:"remote".into()},provider_bypass_headers:vec![],deadline_ms:old}).await.unwrap();
    assert!(store.begin_provider_transport("run",old).await);
    assert!(store.bind_provider_transport("run",ProviderRunTransport::A2a {binding:bcs_domain::a2a::A2aBinding::HttpJson,update_mode:bcs_domain::a2a::A2aUpdateMode::Stream}).await);
    cache.trace.lock().unwrap().clear();(cache,store,scope,old,next)
}
#[tokio::test]
async fn reschedule_extends_all_lifetimes_with_bounded_reads_and_writes() {
    let (cache,store,scope,old,next)=fixture().await;
    assert!(store.reschedule_deadline(&scope,"run",old,next).await.unwrap());
    let trace=cache.trace.lock().unwrap().clone();
    assert_eq!(trace.iter().filter(|call|call.starts_with("batch:")).cloned().collect::<Vec<_>>(),vec!["batch:5","batch:2"]);
    assert_eq!(trace.iter().filter(|call|call.as_str()=="set:UpdateOnly").count(),3);
    assert_eq!(trace.len(),11,"no growing-scope scan or per-alias fanout beyond two identities");
    let scope_key=format!("deadline:botrun:active-scope:{}",serde_json::to_string(&scope).unwrap());
    for key in ["deadline:botrun:run","deadline:botrun:transport:run","deadline:botrun:active:run","deadline:botrun:active-alias:run","deadline:botrun:active-alias:downstream",&scope_key] {
        assert!(matches!(cache.inner.ttl(key).await.unwrap(),CacheTtl::ExpiresIn(ttl) if ttl>Duration::from_secs(290)),"{key}");
    }
    assert_eq!(store.get_context("run").await.unwrap().deadline_ms,next);
    assert_eq!(store.find_active_run("downstream").await.unwrap().unwrap().deadline_ms,next);
}
#[tokio::test]
async fn cache_failures_propagate_before_canonical_budget_is_published() {
    for stage in ["read","scope","write"] {
        let (cache,store,scope,old,next)=fixture().await;
        *cache.fail.lock().unwrap()=Some(stage);
        assert!(store.reschedule_deadline(&scope,"run",old,next).await.is_err(),"{stage}");
        assert_eq!(store.get_context("run").await.unwrap().deadline_ms,old,"{stage}");
    }
}
#[tokio::test]
async fn update_only_does_not_recreate_a_context_that_expired_during_the_write() {
    let (cache,store,scope,old,next)=fixture().await;
    *cache.fail.lock().unwrap()=Some("expire");
    assert!(!store.reschedule_deadline(&scope,"run",old,next).await.unwrap());
    assert!(store.get_context("run").await.is_none());
    assert!(store.find_active_run("run").await.unwrap().is_none());
}

#[tokio::test]
async fn missing_scope_during_expiry_cannot_publish_the_new_canonical_deadline() {
    let (cache,store,scope,old,next)=fixture().await;
    *cache.fail.lock().unwrap()=Some("scope_expire");
    assert!(!store.reschedule_deadline(&scope,"run",old,next).await.unwrap());
    assert_eq!(store.get_context("run").await.unwrap().deadline_ms,old);
}
#[tokio::test]
async fn terminal_races_keep_terminal_protection_for_the_whole_context_lifetime() {
    for terminal_first in [true,false] {
        let (cache,store,scope,old,next)=fixture().await;
        if terminal_first {
            let (terminal,updated)=tokio::join!(store.mark_terminal("run"),store.reschedule_deadline(&scope,"run",old,next));
            assert!(terminal);assert!(!updated.unwrap());
        } else {
            let (updated,terminal)=tokio::join!(store.reschedule_deadline(&scope,"run",old,next),store.mark_terminal("run"));
            assert!(updated.unwrap());assert!(terminal);
        }
        assert!(store.get_context("run").await.unwrap().terminal);
        let CacheTtl::ExpiresIn(context_ttl)=cache.inner.ttl("deadline:botrun:run").await.unwrap() else {panic!("missing context lifetime")};
        let CacheTtl::ExpiresIn(terminal_ttl)=cache.inner.ttl("deadline:botrun:term:run").await.unwrap() else {panic!("missing terminal protection")};
        assert!(terminal_ttl+Duration::from_secs(1)>=context_ttl);
        assert!(!store.reschedule_deadline(&scope,"run",store.get_context("run").await.unwrap().deadline_ms,next+1).await.unwrap());
    }
}


#[tokio::test]
async fn terminal_waits_for_an_inflight_deadline_write_before_choosing_its_ttl() {
    let (cache,store,scope,old,next)=fixture().await;let store=Arc::new(store);
    cache.pause_context.store(true,std::sync::atomic::Ordering::SeqCst);
    let writer=store.clone();
    let budget=tokio::spawn(async move {writer.reschedule_deadline(&scope,"run",old,next).await});
    tokio::time::timeout(Duration::from_secs(2),cache.entered.notified()).await.unwrap();
    let terminal_store=store.clone();
    let mut terminal=tokio::spawn(async move {terminal_store.mark_terminal("run").await});
    assert!(tokio::time::timeout(Duration::from_millis(50),&mut terminal).await.is_err(),"terminal cannot read the old deadline during publication");
    cache.resume.notify_one();assert!(budget.await.unwrap().unwrap());assert!(terminal.await.unwrap());
    assert!(store.get_context("run").await.unwrap().terminal);
    let CacheTtl::ExpiresIn(terminal_ttl)=cache.inner.ttl("deadline:botrun:term:run").await.unwrap() else {panic!("missing terminal protection")};
    assert!(terminal_ttl>Duration::from_secs(290),"terminal protection must retain the updated lifetime");
}
