//! Canonical A2A deadline rescheduling, shared by Memory/Redis/decorated stores.
use bcs_service_api::{ActiveBotRunContext,BotRunContext,BotRunContextPort,BotRunScope,BotRunTransportOwner,ProviderRunTransport};

pub async fn a2a_run_deadline_contract<T:BotRunContextPort + ?Sized>(port:&T,namespace:&str) {
    let run=format!("{namespace}-deadline");
    let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64;
    let original=now+60_000;let waiting=now+600_000;let resumed=now+120_000;
    let scope=BotRunScope {bot_id:format!("{namespace}-bot"),group_id:format!("{namespace}-group"),session_id:format!("{namespace}-session")};
    port.put_context(BotRunContext {run_id:run.clone(),bot_id:scope.bot_id.clone(),group_id:scope.group_id.clone(),
        bcs_session_id:Some(scope.session_id.clone()),deadline_ms:original,terminal:false}).await;
    port.register_active_run(ActiveBotRunContext {canonical_run_id:run.clone(),downstream_run_id:run.clone(),downstream_session_key:Some(scope.session_id.clone()),
        scope:scope.clone(),transport_owner:BotRunTransportOwner::A2a {provider_id:"provider".into(),provider_bot_ref:"remote".into()},
        provider_bypass_headers:vec![],deadline_ms:original}).await.unwrap();
    assert!(port.begin_provider_transport(&run,original).await);
    let transport=ProviderRunTransport::A2a {binding:bcs_domain::a2a::A2aBinding::HttpJson,update_mode:bcs_domain::a2a::A2aUpdateMode::Stream};
    assert!(port.bind_provider_transport(&run,transport).await);
    let wrong=BotRunScope {session_id:"other".into(),..scope.clone()};
    assert!(!port.reschedule_deadline(&wrong,&run,original,waiting).await.unwrap());
    assert!(!port.reschedule_deadline(&scope,&run,original+1,waiting).await.unwrap());
    assert!(!port.reschedule_deadline(&scope,&run,original,now).await.unwrap());
    assert_eq!(port.get_context(&run).await.unwrap().deadline_ms,original);
    assert!(port.reschedule_deadline(&scope,&run,original,waiting).await.unwrap());
    assert_eq!(port.get_context(&run).await.unwrap().deadline_ms,waiting);
    assert_eq!(port.find_active_run(&run).await.unwrap().unwrap().deadline_ms,waiting);
    assert_eq!(port.list_active_runs(&scope).await.unwrap()[0].deadline_ms,waiting);
    assert_eq!(port.get_provider_transport(&run).await,Some(transport));
    assert!(port.reschedule_deadline(&scope,&run,waiting,resumed).await.unwrap());
    assert_eq!(port.get_context(&run).await.unwrap().deadline_ms,resumed);
    assert!(port.try_begin_terminal(&run).await);
    assert!(!port.reschedule_deadline(&scope,&run,resumed,waiting).await.unwrap());
    port.release_terminal(&run).await;
    assert!(port.mark_terminal(&run).await);
    assert!(!port.reschedule_deadline(&scope,&run,resumed,waiting).await.unwrap());
    assert!(port.get_context(&run).await.unwrap().terminal);
}
