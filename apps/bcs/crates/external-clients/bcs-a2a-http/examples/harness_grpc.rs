//! Listening Rust SDK -> MOSN -> Harness smoke, distinct from full BCS E2E.
//! Deployment routes and credentials are environment-only, never logged.
use std::{error::Error,time::Duration};
use bcs_a2a_http::{grpc_mesh::GrpcMeshConfig,card::select_card_with_bindings,projection::{Output,Projection}};
use bcs_service_api::types::a2a_bot::{A2aBinding,A2aInterface,A2aUpdateMode};
use futures::StreamExt;
use serde_json::json;

#[tokio::main]
async fn main()->Result<(),Box<dyn Error>> {
    let mesh:GrpcMeshConfig=serde_json::from_str(&std::env::var("AVERNET_A2A_MESH_CONFIG")?)?;
    mesh.validate().map_err(|_|"invalid explicit mesh configuration")?;
    let card_url=std::env::var("AVERNET_A2A_CARD_URL")?;
    let parsed=reqwest::Url::parse(&card_url)?;
    if parsed.scheme()!="http" || parsed.host_str()!=Some("127.0.0.1") {return Err("smoke requires a loopback Card URL".into());}
    let http=reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(10)).build()?;
    let raw=http.get(card_url).send().await?.error_for_status()?.json().await?;
    let card=select_card_with_bindings(raw,&[],true,&["GRPC"],|i|mesh.supports(&i.url))
        .map_err(|error|format!("Card negotiation failed: {error:?}"))?;
    let interface=A2aInterface {url:card.interface.url,tenant:card.interface.tenant,
        protocol_version:card.interface.protocol_version,binding:A2aBinding::Grpc,update_mode:A2aUpdateMode::Stream};
    let client=mesh.client(&interface,&std::env::var("AVERNET_A2A_BEARER")?,"")?.with_timeout(Duration::from_secs(180));
    let mode=std::env::var("AVERNET_A2A_MODE").unwrap_or_else(|_|"stream".into());
    if !matches!(mode.as_str(),"stream"|"async") {return Err("mode must be stream or async".into());}
    let mut raw_request=json!({"message":{"messageId":format!("grpc-smoke-{}",std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?.as_nanos()),"role":"ROLE_USER",
        "parts":[{"text":"只回复：A2A通过MOSN已连通。不要调用工具。"}]},
        "configuration":{"acceptedOutputModes":["text/plain"]}});
    if mode=="async" {raw_request["configuration"]["returnImmediately"]=json!(true);}
    let request=serde_json::from_value(raw_request)?;
    tokio::time::timeout(Duration::from_secs(190),async {
        let mut stream=if mode=="async" {
            let a2a::SendMessageResponse::Task(task)=client.send_message(&request).await? else {return Err("Task required".into());};
            let query=serde_json::from_value(json!({"id":task.id}))?;
            client.subscribe_to_task(&query).await?
        } else {client.send_streaming_message(&request).await?};
        let mut projection=Projection::default();let mut answer=String::new();let mut completed=false;let mut events=0;
        while let Some(event)=stream.next().await {
            let (next,outputs)=projection.stage(event?).map_err(|_|"invalid event projection")?;
            for output in outputs {
                match output {
                    Output::Delta(text)=>answer.push_str(&text),
                    Output::Status(a2a::TaskState::Completed)=>completed=true,
                    Output::Status(a2a::TaskState::Failed|a2a::TaskState::Canceled|a2a::TaskState::Rejected)=>return Err("Task ended unsuccessfully".into()),
                    _=>{}
                }
            }
            projection=next;events+=1;
        }
        if !completed || answer.is_empty() {return Err("missing completed answer".into());}
        println!("{}",json!({"status":"passed","scope":"Rust SDK -> MOSN -> Harness -> model","mode":mode,"events":events,"answer":answer}));
        Ok::<(),Box<dyn Error>>(())
    }).await??;
    Ok(())
}
