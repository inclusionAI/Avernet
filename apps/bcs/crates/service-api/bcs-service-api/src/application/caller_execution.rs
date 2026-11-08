//! Request-scoped execution credentials. Never serialize these into history or jobs.
#[derive(Clone)]
pub struct ExecutionToken { value: String, token_type: String }

impl ExecutionToken {
    pub fn new(value: String, token_type: String) -> Self { Self { value, token_type } }
    pub fn token_type(&self) -> &str { &self.token_type }
    pub fn expose(&self) -> &str { &self.value }
}

impl std::fmt::Debug for ExecutionToken {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("ExecutionToken(<redacted>)")
    }
}

#[derive(Debug, Clone)]
pub struct CallerExecution {
    pub reference: String,
    pub token: ExecutionToken,
    pub token_type: String,
    pub user_id: String,
    pub binding_id: String,
    pub source_message_id: String,
    pub target_bot_id: String,
}
