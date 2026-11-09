//! Worker-owned external effects. Only canonical saved results cross the trusted inbox boundary.
use crate::*;
pub(crate) async fn execute(
    ports: &RuntimePorts,
    judge: &dyn JudgeExecutor,
    command: Command,
    now: u64,
) -> RuntimeResult<()> {
    let event_id = EventId::new(
        command.command_id.scope.clone(),
        hash("worker-effect", &command.command_id)?,
    );
    if ports
        .inbox
        .find_input(&command.session_id, &event_id)
        .await?
        .is_some()
    {
        return Ok(());
    }
    let (kind, at_ms) = match command.effect {
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
    ports
        .inbox
        .append_and_wake(
            &command.session_id,
            SessionInput {
                event_id,
                at_ms,
                kind,
            },
        )
        .await?;
    Ok(())
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
