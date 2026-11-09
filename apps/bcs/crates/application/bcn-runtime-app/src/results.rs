//! Worker-owned external effects. Only canonical saved results cross the trusted inbox boundary.
use crate::*;
use std::time::Duration;
pub(crate) async fn execute(
    ports: &RuntimePorts,
    judge: &dyn JudgeExecutor,
    leased: &LeasedCommand,
    now: u64,
) -> RuntimeResult<()> {
    let command=&leased.command;
    let event_id = EventId::new(
        command.command_id.scope.clone(),
        hash("worker-effect", &command.command_id)?,
    );
    if let Some(input)=ports.inbox.find_input(&command.session_id,&event_id).await? {
        return complete(ports,&leased.fence,input).await;
    }
    let (kind, at_ms) = match command.effect.clone() {
        EffectIntent::SaveResult { intent } => {
            let result = ports.results.put_if_absent(intent).await?;
            let at = result.saved_at_ms;
            (SessionInputKind::ResultSaved { result }, at)
        }
        EffectIntent::Judge { request } => {
            let branch = judge.execute(request.clone()).await?;
            if !request.choices.contains(&branch) {
                return Err(RuntimeError::InvalidInput(
                    "judge selected an unavailable branch".into(),
                ));
            }
            (
                SessionInputKind::JudgeDecided {
                    task_id: request.task_id,
                    node_id: request.node_id,
                    branch,
                },
                now,
            )
        }
        _ => {
            return Err(RuntimeError::InvalidInput(
                "Worker received connector effect".into(),
            ));
        }
    };
    complete(ports,&leased.fence,SessionInput{event_id,at_ms,kind}).await
}
async fn complete(ports:&RuntimePorts,fence:&CommandDeliveryFence,input:SessionInput)->RuntimeResult<()> {
    loop {
        match ports.commands.complete_effect(fence,input.clone()).await {
            Err(RuntimeError::Contention)=>tokio::time::sleep(Duration::from_millis(50)).await,
            result=>return result.map(|_|()),
        }
    }
}
/// The owned task polls both I/O and its cancellation budgets independently of
/// the business scheduler. Charge acquisition/renewal RTT against each lease.
pub(crate) async fn supervise(ports:RuntimePorts,judge:Arc<dyn JudgeExecutor>,leased:LeasedCommand,now:u64,requested_at:tokio::time::Instant)->RuntimeResult<()> {
    let deadline=tokio::time::Instant::now()+Duration::from_millis(RuntimeDefaults::default().stall_ms);
    let initial_lease=requested_at+Duration::from_millis(leased.lease_ms);
    if tokio::time::Instant::now()>=initial_lease {return Err(RuntimeError::OwnershipLost)}
    let renew=async {
        let mut lease_deadline=initial_lease;
        loop {
            let next=(tokio::time::Instant::now()+Duration::from_millis(1000)).min(lease_deadline);
            tokio::time::sleep_until(next).await;
            if tokio::time::Instant::now()>=lease_deadline {return Err(RuntimeError::OwnershipLost)}
            let started=tokio::time::Instant::now();
            match tokio::time::timeout_at(lease_deadline,ports.commands.renew_effect(&leased.fence)).await {
                Ok(Ok(budget))=>{lease_deadline=started+Duration::from_millis(budget);},
                Ok(Err(RuntimeError::Contention))=>{}, // authoritative reread; never replay business I/O
                Ok(Err(error))=>return Err(error),
                Err(_)=>return Err(RuntimeError::OwnershipLost),
            }
        }
    };
    tokio::select! {
        biased;
        _=tokio::time::sleep_until(deadline)=>Err(RuntimeError::Unavailable("effect deadline reached; durable command retained".into())),
        result=execute(&ports,judge.as_ref(),&leased,now)=>result,
        result=renew=>result,
    }
}

/// Effects need continuous I/O polling, independent of shard-scan tick latency.
/// A pending slot owns its task. Dropping the slot cancels instead of detaching.
pub(crate) type EffectRegistry=Arc<std::sync::Mutex<Vec<tokio::task::AbortHandle>>>;
pub(crate) struct EffectScope(pub EffectRegistry);
impl Drop for EffectScope {
    fn drop(&mut self) { for task in self.0.lock().unwrap().drain(..) {task.abort();} }
}
struct EffectTask(tokio::task::JoinHandle<RuntimeResult<()>>);
impl Drop for EffectTask {fn drop(&mut self){self.0.abort();}}
impl Future for EffectTask {
    type Output=RuntimeResult<()>;
    fn poll(mut self:std::pin::Pin<&mut Self>,cx:&mut std::task::Context<'_>)->std::task::Poll<Self::Output>{
        std::pin::Pin::new(&mut self.0).poll(cx).map(|r|r.unwrap_or_else(|_|Err(RuntimeError::Storage("effect task panicked or was cancelled".into()))))
    }
}
pub(crate) fn drive(mut future:std::pin::Pin<Box<dyn Future<Output=RuntimeResult<()>>+Send>>,registry:&EffectRegistry)
    ->std::pin::Pin<Box<dyn Future<Output=RuntimeResult<()>>+Send>> {
    use futures::FutureExt;
    // Preserve synchronous deterministic tick drivers; only genuinely pending
    // effects need a background task. This performs no extra business retry.
    if let Some(result)=future.as_mut().now_or_never(){return Box::pin(std::future::ready(result))}
    let task=tokio::spawn(future);
    let mut handles=registry.lock().unwrap();handles.retain(|h|!h.is_finished());handles.push(task.abort_handle());
    Box::pin(EffectTask(task))
}

#[cfg(test)]
mod task_tests {
    use super::*;
    #[tokio::test]
    async fn asynchronous_effect_panic_is_a_fatal_runtime_error() {
        let registry=Arc::new(std::sync::Mutex::new(Vec::new()));
        let future=drive(Box::pin(async {tokio::task::yield_now().await;panic!("fixture effect panic")}),&registry);
        assert!(matches!(future.await,Err(RuntimeError::Storage(_))));
    }
    #[tokio::test]
    async fn pending_slot_and_runtime_scope_abort_effect_tasks() {
        use std::sync::atomic::{AtomicBool,Ordering};
        struct Dropped(Arc<AtomicBool>);
        impl Drop for Dropped {fn drop(&mut self){self.0.store(true,Ordering::SeqCst);}}
        for cancel_scope in [false,true] {
            let registry=Arc::new(std::sync::Mutex::new(Vec::new()));
            let dropped=Arc::new(AtomicBool::new(false));let flag=dropped.clone();
            let future=drive(Box::pin(async move{let _guard=Dropped(flag);futures::future::pending::<()>().await;Ok(())}),&registry);
            if cancel_scope {drop(EffectScope(registry));assert!(future.await.is_err());} else {drop(future);}
            tokio::task::yield_now().await;
            assert!(dropped.load(Ordering::SeqCst),"no detached effect task may survive its owning slot/run scope");
        }
    }
}
