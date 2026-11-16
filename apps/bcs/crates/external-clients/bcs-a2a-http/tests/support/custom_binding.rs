use super::*;
use bcs_a2a_binding_api::{Binding, Client, Events, Registry};

struct LocalBinding { endpoints: HashMap<String, String>, revision: Arc<AtomicUsize> }
impl Binding for LocalBinding {
    fn name(&self) -> &str { "LOCAL_TEST" }
    fn resolve(&self, declaration:&Value, _:bool)->Result<String,String> {
        let key=declaration["target"].as_str().ok_or("missing target")?;
        self.fingerprint(key)?;Ok(key.into())
    }
    fn fingerprint(&self,key:&str)->Result<String,String> {
        self.endpoints.get(key).ok_or("unknown target")?;
        Ok(format!("{}",self.revision.load(Ordering::SeqCst)))
    }
    fn client(&self,key:&str,tenant:Option<&str>,bearer:Option<&str>,_:&str,timeout:Duration)->Result<Arc<dyn Client>,String> {
        let endpoint=self.endpoints.get(key).ok_or("unknown target")?;
        let mut headers=HeaderMap::new();
        if let Some(bearer)=bearer {headers.insert("authorization",format!("Bearer {bearer}").parse().unwrap());}
        let http=reqwest::Client::builder().no_proxy().default_headers(headers).timeout(timeout).build().unwrap();
        Ok(Arc::new(LocalClient(bcs_a2a_http::client::bind_rest_client(http,endpoint.clone(),tenant.map(str::to_owned)))))
    }
}
struct LocalClient(bcs_a2a_http::client::RestClient);
#[async_trait]
impl Client for LocalClient {
    async fn send_message(&self,r:&a2a::SendMessageRequest)->Result<a2a::SendMessageResponse,a2a::A2AError>{self.0.send_message(r).await}
    async fn send_streaming_message(&self,r:&a2a::SendMessageRequest)->Result<Events,a2a::A2AError>{self.0.send_streaming_message(r).await}
    async fn get_task(&self,r:&a2a::GetTaskRequest)->Result<a2a::Task,a2a::A2AError>{self.0.get_task(r).await}
    async fn cancel_task(&self,r:&a2a::CancelTaskRequest)->Result<a2a::Task,a2a::A2AError>{self.0.cancel_task(r).await}
    async fn subscribe_to_task(&self,r:&a2a::SubscribeToTaskRequest)->Result<Events,a2a::A2AError>{self.0.subscribe_to_task(r).await}
}

fn base(f:&Fixture)->String {
    let BotDeliveryTarget::A2a {interface,..}=&f.target else {panic!("A2A");};interface.url.clone()
}
async fn custom_target(f:&mut Fixture,key:&str) {
    use bcs_service_api::port::a2a_card::A2aCardPort;
    let raw=json!({"name":"Example","description":"Example","version":"1", "capabilities":{"streaming":true},
        "defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"skills":[],
        "supportedInterfaces":[{"protocolBinding":"LOCAL_TEST","protocolVersion":"1.0","target":key}]});
    let fetcher=bcs_a2a_http::card_fetch::HttpA2aCardFetcher::new(OutboundUrlGuard::new(false,false),vec![]);
    let selected=fetcher.validate(raw.clone(),true).await.unwrap();assert_eq!(selected.raw,raw);
    let BotDeliveryTarget::A2a {interface,..}=&mut f.target else {panic!("A2A");};
    *interface=selected.interface;
}

#[tokio::test]
async fn custom_admission_streaming_persistence_and_abort_use_the_same_plugin() {
    let mut complete=fixture(Mode::Complete).await;
    let mut wait=fixture(Mode::Wait).await;
    let revision=Arc::new(AtomicUsize::new(1));
    let binding=LocalBinding {endpoints:HashMap::from([("complete".into(),base(&complete)),("wait".into(),base(&wait))]),revision:revision.clone()};
    bcs_a2a_binding_api::install(Registry::new(vec![Arc::new(binding)]).unwrap()).unwrap();
    custom_target(&mut complete,"complete").await;custom_target(&mut wait,"wait").await;
    assert!(complete.transport.is_available(&complete.target).await);
    assert!(complete.transport.deliver(command(&complete,"custom-complete").await).await.unwrap().delivered);
    completed(&complete,"custom-complete").await;
    let record=complete.runs.get("custom-complete").await.unwrap().unwrap();
    assert_eq!(record.interface.binding,A2aBinding::Custom);assert_eq!(record.remote_task_id.as_deref(),Some("remote-1"));
    assert_eq!(record.scope.route_fingerprint.as_deref(),Some("1"));
    let text:String=complete.ingest.events.lock().unwrap().iter().filter_map(|e|e.event_payload["delta_text"].as_str()).collect();
    assert_eq!(text,"你好世界");assert!(complete.agent.queries.load(Ordering::SeqCst)>0);
    assert!(wait.transport.deliver(command(&wait,"custom-wait").await).await.unwrap().delivered);
    let abort=BotAbortDeliveryCommand {command_id:"abort".into(),target:wait.target.clone(),run_id:Some("custom-wait".into()),
        group_id:"group".into(),session_id:"session".into(),timeout_ms:1000,provider_bypass_headers:vec![]};
    revision.store(2,Ordering::SeqCst);
    assert!(wait.transport.abort(abort.clone()).await.is_err());
    assert!(wait.agent.cancels.lock().unwrap().is_empty());
    revision.store(1,Ordering::SeqCst);
    wait.transport.abort(abort).await.unwrap();
    assert_eq!(*wait.agent.cancels.lock().unwrap(),["remote-1"]);
}
