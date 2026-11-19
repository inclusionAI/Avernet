use super::*;
pub(super) async fn visit(
    ports: &SupervisorPorts,
    config: &SupervisorConfig,
    job: &mut Job,
    now: TimestampMillis,
) -> SupervisorReport {
    let mut report = SupervisorReport {
        visited: 1,
        ..Default::default()
    };
    let Some(_permit) = ports.scan_budget.try_io() else {
        report.deferred = 1;
        job.ready = Instant::now() + config.retry_backoff;
        return report;
    };
    let (logs, sessions) = match job.phase {
        Phase::Load => (1, 0),
        Phase::Spec(_) => (0, 1),
        _ => (0, 0),
    };
    if logs + sessions > 0 {
        match ports.scan_budget.claim(ScanClass::Dispatch, logs, sessions) {
            Ok(true) => {}
            Ok(false) => {
                report.deferred = 1;
                job.ready = Instant::now() + config.retry_backoff;
                return report;
            }
            Err(_) => {
                retry(job, config);
                report.failures = 1;
                return report;
            }
        }
    }
    // The original phase remains stored until the one bounded IO completes. A
    // canceled tick therefore retains stable admission/reschedule identities.
    let phase = job.phase.clone();
    let future = async {
        match phase {
            Phase::Load => match ports.dispatch.get(&job.did).await? {
                Some(record) => {
                    let facts = Facts::from_record(&job.did, job.shard, record)?;
                    Ok(if facts.terminal.is_some() {
                        Phase::Spec(facts)
                    } else if facts.holder.is_some() {
                        Phase::Holder(facts)
                    } else {
                        Phase::Notify(facts)
                    })
                }
                None => Ok(Phase::Done),
            },
            Phase::Holder(facts) => {
                let holder = facts
                    .holder
                    .as_ref()
                    .ok_or_else(|| invalid("supervisor_missing_holder"))?;
                match ports.membership.liveness(holder).await {
                    Ok(Liveness::Alive | Liveness::Dead) => Ok(Phase::Notify(facts)),
                    _ => {
                        report.deferred += 1;
                        Ok(Phase::Settle {
                            revision: facts.revision,
                            at: next_at(now, config.unknown_interval),
                        })
                    }
                }
            }
            Phase::Notify(facts) => {
                match ports.dispatch.notify(&facts.pool, &job.did).await {
                    Ok(()) => report.notified += 1,
                    Err(_) => report.failures += 1,
                };
                let next = next_at(now, config.recheck_interval);
                let at = if facts.deadline.0 > now.0 {
                    TimestampMillis(next.0.min(facts.deadline.0))
                } else {
                    next
                };
                Ok(Phase::Settle {
                    revision: facts.revision,
                    at,
                })
            }
            Phase::Spec(facts) => {
                let spec = ports
                    .sessions
                    .get_spec(&facts.sid)
                    .await?
                    .ok_or_else(|| invalid("supervisor_session_missing"))?;
                let request = facts.request(job.shard, spec)?;
                Ok(Phase::Admit {
                    facts,
                    request: Box::new(request),
                })
            }
            Phase::Admit { facts, request } => {
                let expected = (
                    request.input.sid.clone(),
                    request.input.tid.clone(),
                    request.input.input_id.clone(),
                );
                match ports.inbox.admit(*request).await? {
                    AdmitOutcome::Accepted(receipt) | AdmitOutcome::Duplicate(receipt) => {
                        if (receipt.sid, receipt.tid, receipt.input_id) != expected {
                            return Err(invalid("supervisor_return_receipt"));
                        }
                        report.returned += 1;
                        Ok(Phase::Settle {
                            revision: facts.revision,
                            at: now,
                        })
                    }
                    AdmitOutcome::ContentConflict => Err(invalid("supervisor_return_conflict")),
                    AdmitOutcome::Quarantined => Err(invalid("supervisor_return_rejected")),
                }
            }
            Phase::Settle { revision, at } => {
                match ports.dispatch.reschedule(&job.did, revision, at).await? {
                    RecheckOutcome::Applied | RecheckOutcome::Ended => {
                        report.settled += 1;
                        Ok(Phase::Done)
                    }
                    RecheckOutcome::NeedsRecheck => {
                        report.deferred += 1;
                        // Retain durable due and yield this local slot; a hot
                        // revision must not monopolize a full pending queue.
                        Ok(Phase::Done)
                    }
                }
            }
            Phase::Done => Ok(Phase::Done),
        }
    };
    match tokio::time::timeout(config.io_timeout, future).await {
        Ok(Ok(next)) => {
            job.phase = next;
            job.failures = 0;
            job.ready = Instant::now();
        }
        _ => {
            report.failures += 1;
            retry(job, config);
        }
    }
    report
}
fn retry(job: &mut Job, config: &SupervisorConfig) {
    job.failures = job.failures.saturating_add(1);
    // Local cache eviction never settles/deletes durable due. The retained
    // terminal evidence reconstructs the exact same input after rediscovery.
    if job.failures >= 3 {
        job.phase = Phase::Done;
        return;
    }
    let delay = config
        .retry_backoff
        .saturating_mul(1u32 << job.failures.min(5))
        .min(Duration::from_secs(5));
    job.ready = Instant::now() + delay;
}
