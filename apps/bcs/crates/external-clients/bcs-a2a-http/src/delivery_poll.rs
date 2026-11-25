//! Non-streaming tracking uses bounded snapshots, never subscription fallback.
use super::*;

impl A2aHttpTransport {
    pub(super) async fn drive_poll(&self,record:&mut A2aRunRecord,projection:&mut Projection,
        mut stop:tokio::sync::watch::Receiver<bool>)->ServiceResult<()> {
        let mut failures=0;
        loop {
            let remaining=record.deadline_ms.saturating_sub(now_ms());
            if remaining==0 {return Err(invalid("execution budget expired"));}
            tokio::select! {
                _=stop.changed()=>return Ok(()),
                _=tokio::time::sleep(Duration::from_millis(remaining.min(1_000)))=>{}
            }
            let remaining=record.deadline_ms.saturating_sub(now_ms());
            if remaining==0 {return Err(invalid("execution budget expired"));}
            let result=tokio::select! {
                _=stop.changed()=>return Ok(()),
                result=tokio::time::timeout(Duration::from_millis(remaining.min(15_000)),self.snapshot(record))=>result,
            };
            match result {
                Ok(Ok(task))=>{
                    if self.apply(record,projection,StreamResponse::Task(task)).await? {return Ok(());}
                    failures=0;
                }
                _=>{
                    failures+=1;
                    if failures>=3 {return Err(invalid("Task polling failure budget exhausted"));}
                }
            }
        }
    }
}
