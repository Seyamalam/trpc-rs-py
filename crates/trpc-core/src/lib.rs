//! `trpc-core` — the pure-Rust tRPC v11 protocol core.
//!
//! This crate has **no `PyO3` dependency** so it can be tested and benchmarked
//! on its own (MISSION.md section 2). Current functionality includes:
//!
//! * tRPC v11 HTTP protocol handling: queries (GET or POST), mutations (POST),
//!   batching (`?batch=1`), result/error envelopes and HTTP status mapping.
//! * The router table: procedure path lookup without allocation on the hot path.
//! * JSON parsing/serialization and request limits for body size, batch size,
//!   and JSON nesting depth.
//!
//! Later milestones will add subscriptions over Server-Sent Events, procedure
//! timeouts, and TypeScript type generation for the `AppRouter` type.
//!
//! The `PyO3` bindings live in [`crates/trpc-py`] and must stay thin: whole
//! requests and whole batches cross the Python boundary in one call.
//!
//! [`crates/trpc-py`]: ../trpc_py/index.html

#![deny(unsafe_code)]

pub mod error;
mod json;
pub mod protocol;

/// Crate version, sourced from the workspace version.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_matches_workspace() {
        let manifest = include_str!("../../../Cargo.toml");
        let section = manifest
            .split("[workspace.package]")
            .nth(1)
            .expect("workspace.package section");
        let expected = section
            .lines()
            .find_map(|line| line.trim().strip_prefix("version = \""))
            .and_then(|rest| rest.strip_suffix('"'))
            .expect("workspace version");
        assert_eq!(VERSION, expected);
    }
}
