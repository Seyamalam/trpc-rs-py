//! `trpc-core` — the pure-Rust tRPC v11 protocol core.
//!
//! This crate has **no `PyO3` dependency** so it can be tested and benchmarked
//! on its own (MISSION.md section 2). It owns:
//!
//! * tRPC v11 HTTP protocol handling: queries (GET), mutations (POST),
//!   batching (`?batch=1`), result/error envelopes and HTTP status mapping.
//! * Subscriptions over Server-Sent Events (`httpSubscriptionLink`).
//! * The router table: procedure path lookup without allocation on the hot path.
//! * JSON parsing/serialization.
//! * Request limits: body size, batch size, JSON nesting depth, timeouts.
//! * TypeScript type generation for the `AppRouter` type.
//!
//! The `PyO3` bindings live in [`crates/trpc-py`] and must stay thin: whole
//! requests and whole batches cross the Python boundary in one call.
//!
//! [`crates/trpc-py`]: ../trpc_py/index.html

#![deny(unsafe_code)]

pub mod error;

/// Crate version, sourced from the workspace version.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_matches_workspace() {
        assert_eq!(VERSION, "0.1.0");
    }
}
