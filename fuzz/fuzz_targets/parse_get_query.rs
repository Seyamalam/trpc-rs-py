//! Fuzzes the tRPC GET-query parse path: percent-decoding of `input=`, the
//! `batch=1` flag, procedure-path charset validation, and the batch input
//! object — then routes the parsed calls and builds the response envelope so
//! serialization of attacker-shaped values is exercised too.
//!
//! Input layout (all offsets in bytes):
//!   [0..4]   limit knobs (see `trpc_core_fuzz::limits`)
//!   [4..]    path `\x00` query   — split once on the first NUL

#![no_main]

use libfuzzer_sys::fuzz_target;
use trpc_core::protocol::{
    HttpMethod, ProcedureKind, Request, Router, build_response, parse_request,
};
use trpc_core_fuzz::{FIELD_SEP, bounded, fields, limits, text};

fuzz_target!(|data: &[u8]| {
    let data = bounded(data);
    let limits = limits(&data[..4.min(data.len())]);
    let Some(rest) = data.get(4..) else { return };
    let parts = fields(rest, FIELD_SEP, 2);
    let path = text(parts[0]);
    let query = text(parts[1]);

    let request = Request {
        method: HttpMethod::Get,
        path: &path,
        query: &query,
        body: b"",
        content_length: None,
    };
    let Ok(parsed) = parse_request(request, limits) else {
        return;
    };

    // Push parsed calls through routing and response construction; the input
    // values are echoed back so envelope serialization sees fuzzed JSON.
    let mut router = Router::new();
    router.register("echo", 1, ProcedureKind::Query);
    let results = parsed
        .calls
        .iter()
        .map(|call| {
            router
                .route(&call.path, HttpMethod::Get)
                .map(|_| call.input.clone())
                .map_err(|error| error.clone())
        })
        .collect();
    let _ = build_response(results, parsed.batched);
});
