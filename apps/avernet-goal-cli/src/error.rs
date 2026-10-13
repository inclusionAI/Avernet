use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum ExitCode {
    Success = 0,
    InvalidInput = 2,
    ActionRequired = 3,
    PartialSuccess = 4,
    Unauthorized = 5,
    Unavailable = 6,
    ExecutionFailed = 7,
}

#[derive(Debug, Error)]
pub enum AppError {
    #[error("{0}")]
    InvalidInput(String),
    #[error("{0}")]
    Unauthorized(String),
    #[error("{0}")]
    Unavailable(String),
    #[error("{0}")]
    Remote(String),
    #[error("{0}")]
    Io(#[from] std::io::Error),
    #[error("{0}")]
    Serialization(String),
}

impl AppError {
    pub fn exit_code(&self) -> ExitCode {
        match self {
            Self::InvalidInput(_) | Self::Serialization(_) => ExitCode::InvalidInput,
            Self::Unauthorized(_) => ExitCode::Unauthorized,
            Self::Unavailable(_) => ExitCode::Unavailable,
            Self::Remote(_) | Self::Io(_) => ExitCode::ExecutionFailed,
        }
    }
}

impl From<serde_json::Error> for AppError {
    fn from(value: serde_json::Error) -> Self {
        Self::Serialization(value.to_string())
    }
}

impl From<serde_yaml::Error> for AppError {
    fn from(value: serde_yaml::Error) -> Self {
        Self::Serialization(value.to_string())
    }
}
