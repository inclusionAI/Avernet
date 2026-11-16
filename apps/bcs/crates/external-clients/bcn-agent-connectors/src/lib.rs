//! Target-bound protocol adapters. Concrete wiring belongs to bootstrap.
pub mod a2a;
mod errors;
mod projection;
pub const MAX_OUTPUT_BYTES: usize = 64 * 1024;
pub mod bcn_sse;
mod provider_stream;
