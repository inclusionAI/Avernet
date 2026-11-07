use bcs_service_api::core::run_budget::*;

pub fn run_budget_core_service_contract_tests<T:RunBudgetCoreService+?Sized>(core:&T) {
    let limits=RunBudgetLimits {input_wait_ms:1000,total_run_ms:10_000};
    let started=core.start(1000,1100,limits).unwrap();
    assert_eq!(started.deadline_ms,1100);
    let paused=core.pause(&started.state,1050,9000).unwrap();
    assert_eq!(paused.state.remaining_execution_ms,50);assert_eq!(paused.deadline_ms,2050);
    let repeated=core.pause(&paused.state,1550,10_000).unwrap();
    assert_eq!(repeated,paused,"new observations cannot renew the wait");
    let resumed=core.resume(&paused.state,1550).unwrap();
    assert_eq!(resumed.deadline_ms,1600,"human wait does not consume execution time");
    let second=core.pause(&resumed.state,1570,9000).unwrap();
    assert_eq!(second.state.remaining_execution_ms,30);
    assert_eq!(second.state.total_deadline_ms,11_000,"later inputs cannot renew total budget");
    assert!(core.resume(&paused.state,2050).is_err());
    assert!(core.resume(&started.state,1050).is_err());
    assert!(core.pause(&started.state,1100,9000).is_err());
    assert!(core.pause(&started.state,999,9000).is_err());
    assert!(core.resume(&paused.state,1049).is_err());
    assert!(core.start(1000,1000,limits).is_err());
    assert!(core.start(u64::MAX-1,u64::MAX,limits).is_err());
    assert!(core.start(1000,1100,RunBudgetLimits {input_wait_ms:0,..limits}).is_err());
    let capped=core.start(1000,1500,RunBudgetLimits {input_wait_ms:1000,total_run_ms:100}).unwrap();
    assert_eq!(capped.deadline_ms,1100);
    let waiting=core.pause(&capped.state,1050,10_000).unwrap();
    assert_eq!(waiting.deadline_ms,1100);
    let near_end=core.resume(&waiting.state,1090).unwrap();assert_eq!(near_end.deadline_ms,1100);
    assert!(core.resume(&waiting.state,1100).is_err());
    let remote_short=core.pause(&started.state,1050,1070).unwrap();assert_eq!(remote_short.deadline_ms,1070);
    assert!(core.pause(&started.state,1050,1049).is_err());
    assert_eq!(serde_json::from_value::<RunBudgetState>(serde_json::to_value(&second.state).unwrap()).unwrap(),second.state);
}
