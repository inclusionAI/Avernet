use std::collections::HashMap;

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use thiserror::Error;

use crate::config::{BehaviorConfig, StateScope, SupervisorAssignment, SupervisorCompletion};

#[derive(Debug, Clone)]
pub struct BehaviorInput {
    pub session_key: String,
    pub bcs_group_id: String,
    pub bcs_session_id: Option<String>,
    pub message_text: String,
    pub context_message: String,
    pub task_id: Option<String>,
    pub sender_name: String,
    pub recipient_role: Option<String>,
    pub group_type: Option<String>,
    pub participants: Vec<String>,
}

impl BehaviorInput {
    pub fn effective_session_key(&self) -> &str {
        self.bcs_session_id
            .as_deref()
            .filter(|value| !value.is_empty())
            .or_else(|| (!self.session_key.is_empty()).then_some(self.session_key.as_str()))
            .unwrap_or(self.bcs_group_id.as_str())
    }
}

#[derive(Debug, Clone)]
pub enum BehaviorOutcome {
    Reply(String),
    StartSupervisor(SupervisorStart),
}

#[derive(Debug, Clone)]
pub struct SupervisorStart {
    pub assignment: SupervisorAssignment,
    pub completion: SupervisorCompletion,
    pub summary_template: String,
}

#[derive(Debug, Error)]
pub enum BehaviorError {
    #[error("echo output size overflow")]
    OutputSizeOverflow,
    #[error("unable to reserve memory for echo output")]
    ResourceExhausted,
}

pub struct BehaviorRuntime {
    kind: BehaviorKind,
}

enum BehaviorKind {
    Fixed {
        replies: Vec<String>,
        scope: StateScope,
        cursors: HashMap<String, usize>,
    },
    RandomReply {
        replies: Vec<String>,
        scope: StateScope,
        random: ScopedRandom,
    },
    Echo {
        repeat: u64,
        separator: String,
        prefix: String,
        suffix: String,
    },
    RandomNumber {
        min: i64,
        max: i64,
        scope: StateScope,
        random: ScopedRandom,
    },
    TaskWorker {
        result: Box<BehaviorRuntime>,
    },
    Supervisor {
        start: SupervisorStart,
    },
}

struct ScopedRandom {
    base_seed: u64,
    generators: HashMap<String, StdRng>,
}

impl BehaviorRuntime {
    pub fn new(config: &BehaviorConfig) -> Self {
        let kind = match config {
            BehaviorConfig::Fixed { replies, scope } => BehaviorKind::Fixed {
                replies: replies.clone(),
                scope: *scope,
                cursors: HashMap::new(),
            },
            BehaviorConfig::RandomReply {
                replies,
                seed,
                scope,
            } => BehaviorKind::RandomReply {
                replies: replies.clone(),
                scope: *scope,
                random: ScopedRandom::new(*seed),
            },
            BehaviorConfig::Echo {
                repeat,
                separator,
                prefix,
                suffix,
            } => BehaviorKind::Echo {
                repeat: *repeat,
                separator: separator.clone(),
                prefix: prefix.clone(),
                suffix: suffix.clone(),
            },
            BehaviorConfig::RandomNumber {
                min,
                max,
                seed,
                scope,
            } => BehaviorKind::RandomNumber {
                min: *min,
                max: *max,
                scope: *scope,
                random: ScopedRandom::new(*seed),
            },
            BehaviorConfig::TaskWorker { result } => BehaviorKind::TaskWorker {
                result: Box::new(Self::new(result)),
            },
            BehaviorConfig::Supervisor {
                assignment,
                completion,
                summary_template,
            } => BehaviorKind::Supervisor {
                start: SupervisorStart {
                    assignment: assignment.clone(),
                    completion: completion.clone(),
                    summary_template: summary_template.clone(),
                },
            },
        };
        Self { kind }
    }

    pub fn handle_send(&mut self, input: &BehaviorInput) -> Result<BehaviorOutcome, BehaviorError> {
        match &mut self.kind {
            BehaviorKind::Fixed {
                replies,
                scope,
                cursors,
            } => {
                let key = scope_key(*scope, input);
                let cursor = cursors.entry(key).or_insert(0);
                let reply = replies[*cursor % replies.len()].clone();
                *cursor = (*cursor + 1) % replies.len();
                Ok(BehaviorOutcome::Reply(reply))
            }
            BehaviorKind::RandomReply {
                replies,
                scope,
                random,
            } => {
                let key = scope_key(*scope, input);
                let index = random.generator(&key).gen_range(0..replies.len());
                Ok(BehaviorOutcome::Reply(replies[index].clone()))
            }
            BehaviorKind::Echo {
                repeat,
                separator,
                prefix,
                suffix,
            } => Ok(BehaviorOutcome::Reply(build_echo(
                &input.message_text,
                *repeat,
                separator,
                prefix,
                suffix,
            )?)),
            BehaviorKind::RandomNumber {
                min,
                max,
                scope,
                random,
            } => {
                let key = scope_key(*scope, input);
                let number = random.generator(&key).gen_range(*min..=*max);
                Ok(BehaviorOutcome::Reply(number.to_string()))
            }
            BehaviorKind::TaskWorker { result } => result.handle_send(input),
            BehaviorKind::Supervisor { start } => {
                Ok(BehaviorOutcome::StartSupervisor(start.clone()))
            }
        }
    }

    pub fn clear_session(&mut self, session_key: &str) {
        match &mut self.kind {
            BehaviorKind::Fixed { cursors, .. } => {
                cursors.remove(session_key);
            }
            BehaviorKind::RandomReply { random, .. }
            | BehaviorKind::RandomNumber { random, .. } => {
                random.generators.remove(session_key);
            }
            BehaviorKind::TaskWorker { result } => result.clear_session(session_key),
            BehaviorKind::Echo { .. } | BehaviorKind::Supervisor { .. } => {}
        }
    }
}

impl ScopedRandom {
    fn new(seed: Option<u64>) -> Self {
        Self {
            base_seed: seed.unwrap_or_else(rand::random),
            generators: HashMap::new(),
        }
    }

    fn generator(&mut self, key: &str) -> &mut StdRng {
        self.generators
            .entry(key.to_string())
            .or_insert_with(|| StdRng::seed_from_u64(derive_seed(self.base_seed, key)))
    }
}

fn scope_key(scope: StateScope, input: &BehaviorInput) -> String {
    match scope {
        StateScope::Session => input.effective_session_key().to_string(),
        StateScope::Bot => "__bot__".to_string(),
    }
}

fn derive_seed(base_seed: u64, key: &str) -> u64 {
    let mut hash = 0xcbf2_9ce4_8422_2325_u64 ^ base_seed;
    for byte in key.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

fn build_echo(
    message: &str,
    repeat: u64,
    separator: &str,
    prefix: &str,
    suffix: &str,
) -> Result<String, BehaviorError> {
    if message.is_empty() && separator.is_empty() {
        let output_bytes = prefix
            .len()
            .checked_add(suffix.len())
            .ok_or(BehaviorError::OutputSizeOverflow)?;
        let mut output = String::new();
        output
            .try_reserve_exact(output_bytes)
            .map_err(|_| BehaviorError::ResourceExhausted)?;
        output.push_str(prefix);
        output.push_str(suffix);
        return Ok(output);
    }

    let count = usize::try_from(repeat).map_err(|_| BehaviorError::OutputSizeOverflow)?;
    let repeated_bytes = message
        .len()
        .checked_mul(count)
        .ok_or(BehaviorError::OutputSizeOverflow)?;
    let separator_bytes = separator
        .len()
        .checked_mul(count.saturating_sub(1))
        .ok_or(BehaviorError::OutputSizeOverflow)?;
    let output_bytes = prefix
        .len()
        .checked_add(repeated_bytes)
        .and_then(|size| size.checked_add(separator_bytes))
        .and_then(|size| size.checked_add(suffix.len()))
        .ok_or(BehaviorError::OutputSizeOverflow)?;
    if output_bytes > isize::MAX as usize {
        return Err(BehaviorError::OutputSizeOverflow);
    }

    let mut output = String::new();
    output
        .try_reserve_exact(output_bytes)
        .map_err(|_| BehaviorError::ResourceExhausted)?;
    output.push_str(prefix);
    for index in 0..count {
        if index > 0 {
            output.push_str(separator);
        }
        output.push_str(message);
    }
    output.push_str(suffix);
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(session: &str, message: &str) -> BehaviorInput {
        BehaviorInput {
            session_key: session.to_string(),
            bcs_group_id: "group".to_string(),
            bcs_session_id: None,
            message_text: message.to_string(),
            context_message: message.to_string(),
            task_id: None,
            sender_name: "human".to_string(),
            recipient_role: None,
            group_type: None,
            participants: Vec::new(),
        }
    }

    fn reply(runtime: &mut BehaviorRuntime, input: &BehaviorInput) -> String {
        match runtime
            .handle_send(input)
            .unwrap_or_else(|error| panic!("{error}"))
        {
            BehaviorOutcome::Reply(reply) => reply,
            BehaviorOutcome::StartSupervisor(_) => panic!("unexpected supervisor"),
        }
    }

    #[test]
    fn fixed_cycles_per_session() {
        let mut runtime = BehaviorRuntime::new(&BehaviorConfig::Fixed {
            replies: vec!["你好".to_string(), "好的".to_string()],
            scope: StateScope::Session,
        });

        assert_eq!(reply(&mut runtime, &input("a", "x")), "你好");
        assert_eq!(reply(&mut runtime, &input("a", "x")), "好的");
        assert_eq!(reply(&mut runtime, &input("a", "x")), "你好");
        assert_eq!(reply(&mut runtime, &input("b", "x")), "你好");
    }

    #[test]
    fn fixed_single_reply_is_constant() {
        let mut runtime = BehaviorRuntime::new(&BehaviorConfig::Fixed {
            replies: vec!["你好".to_string()],
            scope: StateScope::Session,
        });

        assert_eq!(reply(&mut runtime, &input("a", "x")), "你好");
        assert_eq!(reply(&mut runtime, &input("a", "x")), "你好");
    }

    #[test]
    fn random_reply_is_seeded_and_in_pool() {
        let config = BehaviorConfig::RandomReply {
            replies: vec!["a".to_string(), "b".to_string(), "c".to_string()],
            seed: Some(42),
            scope: StateScope::Session,
        };
        let mut first = BehaviorRuntime::new(&config);
        let mut second = BehaviorRuntime::new(&config);

        let first_values = (0..8)
            .map(|_| reply(&mut first, &input("session", "x")))
            .collect::<Vec<_>>();
        let second_values = (0..8)
            .map(|_| reply(&mut second, &input("session", "x")))
            .collect::<Vec<_>>();

        assert_eq!(first_values, second_values);
        assert!(
            first_values
                .iter()
                .all(|value| ["a", "b", "c"].contains(&value.as_str()))
        );
    }

    #[test]
    fn echo_repeats_without_business_limit() {
        let mut runtime = BehaviorRuntime::new(&BehaviorConfig::Echo {
            repeat: 3,
            separator: "、".to_string(),
            prefix: "复读：".to_string(),
            suffix: "。".to_string(),
        });

        assert_eq!(
            reply(&mut runtime, &input("session", "你好")),
            "复读：你好、你好、你好。"
        );
    }

    #[test]
    fn echo_reports_address_space_overflow() {
        let result = build_echo("x", u64::MAX, "", "", "");

        assert!(matches!(result, Err(BehaviorError::OutputSizeOverflow)));
    }

    #[test]
    fn echo_avoids_unbounded_work_for_empty_repeated_content() {
        let result = build_echo("", u64::MAX, "", "前", "后")
            .unwrap_or_else(|error| panic!("empty echo failed: {error}"));

        assert_eq!(result, "前后");
    }

    #[test]
    fn random_number_is_inclusive_and_seeded() {
        let config = BehaviorConfig::RandomNumber {
            min: -2,
            max: 2,
            seed: Some(9),
            scope: StateScope::Bot,
        };
        let mut first = BehaviorRuntime::new(&config);
        let mut second = BehaviorRuntime::new(&config);

        let first_values = (0..10)
            .map(|_| reply(&mut first, &input("a", "x")))
            .collect::<Vec<_>>();
        let second_values = (0..10)
            .map(|_| reply(&mut second, &input("b", "x")))
            .collect::<Vec<_>>();

        assert_eq!(first_values, second_values);
        assert!(first_values.iter().all(|value| {
            value
                .parse::<i64>()
                .map(|number| (-2..=2).contains(&number))
                .unwrap_or(false)
        }));
    }
}
