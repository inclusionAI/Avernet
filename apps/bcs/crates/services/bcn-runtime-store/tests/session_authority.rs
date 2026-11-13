mod common;
use common::*;
use bcn_runtime_api::{core::SessionRunner,port::repo::*};
use bcn_runtime_model::*;
use bcn_runtime_storage_api::AtomicStore;
use bcn_session_runner::DirectA2aRunner;
fn next(mut r:AdmissionRequest, principal:&str)->AdmissionRequest {
 r.caller.principal=principal.into();r.admission_id.value="next".into();r.task.task_id.value="next".into();r.input.event_id.value="next".into();
 if let SessionInputKind::Submit{task}=&mut r.input.kind{task.task_id=r.task.task_id.clone();}r
}
#[tokio::test]
async fn session_owner_is_durable_and_denial_has_no_side_effects() {
 let (repo,atomic,req)=fixture();repo.admit(req.clone()).await.unwrap();
 let page=atomic.scan_page(&scope(),"bcn-runtime-v1","session-header/",None,1).await.unwrap();
 let value:serde_json::Value=serde_json::from_slice(&page.entries[0].entry.value).unwrap();
 assert_eq!(value["data"]["owner"],"alice");
 let other=next(req.clone(),"bob");assert_eq!(repo.admit(other.clone()).await,Err(RuntimeError::AccessDenied));
 assert!(repo.get_receipt(&other.admission_id).await.unwrap().is_none());
 assert_eq!(repo.get_task(&other.caller,&other.task.task_id).await,Err(RuntimeError::NotFound));
 assert_eq!(repo.read_batch(&req.spec.session_id,0,100).await.unwrap().len(),1);
 assert!(repo.admit(next(req,"alice")).await.is_ok());
}
#[tokio::test]
async fn ownerless_header_fails_closed_but_keeps_receipts_reads_and_checkpoint_recovery() {
 let (repo,atomic,req)=fixture();let receipt=repo.admit(req.clone()).await.unwrap();
 let page=atomic.scan_page(&scope(),"bcn-runtime-v1","session-header/",None,1).await.unwrap();
 let row=&page.entries[0];let mut value:serde_json::Value=serde_json::from_slice(&row.entry.value).unwrap();value["data"].as_object_mut().unwrap().remove("owner");
 atomic.transact(&scope(),"bcn-runtime-v1",AtomicBatch{conditions:vec![],mutations:vec![Mutation::Put{key:row.key.clone(),value:serde_json::to_vec(&value).unwrap()}]}).await.unwrap();
 assert_eq!(repo.admit(next(req.clone(),"alice")).await,Err(RuntimeError::AccessDenied));
 assert_eq!(repo.admit(req.clone()).await.unwrap(),receipt);
 assert!(repo.get_task(&req.caller,&req.task.task_id).await.is_ok());
 let spec=repo.load_spec(&req.spec.session_id).await.unwrap().unwrap();
 let fence=repo.claim_shard(session_shard(&spec.session_id),node("one"),None).await.unwrap();
 repo.activate(spec.clone(),DirectA2aRunner.initialize(&spec).unwrap(),fence).await.unwrap();
 assert!(repo.load(&spec.session_id).await.unwrap().is_some());
}
