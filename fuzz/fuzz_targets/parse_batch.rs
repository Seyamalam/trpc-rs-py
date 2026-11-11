//! Fuzzes batched tRPC requests specifically: comma-separated procedure paths,
//! the batch input object's numeric-key validation, sparse/missing indexes,
//! and percent-decoded GET batch inputs.
//!
//! The `batch=1` flag is forced so every run reaches `batch_inputs`; the
//! method byte picks GET (input arrives percent-encoded inside the query
//! string) or POST (input is the raw body).
//!
//! Input layout (all offsets in bytes):
//!   [0..4]   limit knobs (see `trpc_core_fuzz::limits`)
//!   [4]      method: even → GET, odd → POST
//!   [5..]    paths `\x00` input — split once on the first NUL

#![no_main]

use libfuzzer_sys::fuzz_target;
use trpc_core::protocol::{
    HttpMethod, ProcedureKind, Request, Router, build_response, parse_request,
};
use trpc_core_fuzz::{FIELD_SEP, bounded, fields, limits, text};

fuzz_target!(|data: &[u8]| {
    let data = bounded(data);
    let limits = limits(&data[..4.min(data.len())]);
    let Some(&method_byte) = data.get(4) else {
        return;
    };
    let Some(rest) = data.get(5..) else { return };
    let parts = fields(rest, FIELD_SEP, 2);
    let path = text(parts[0]);
    let input = parts[1];

    let (method, query, body) = if method_byte & 1 == 0 {
        // GET: the fuzzed bytes are the raw `input` parameter value; the
        // parser must percent-decode them before JSON parsing.
        (
            HttpMethod::Get,
            format!("batch=1&input={}", text(input)),
            &[][..],
        )
    } else {
        (HttpMethod::Post, "batch=1".to_owned(), input)
    };
    let request = Request {
        method,
        path: &path,
        query: &query,
        body,
        content_length: (method == HttpMethod::Post).then_some(body.len()),
    };
    let Ok(parsed) = parse_request(request, limits) else {
        return;
    };

    let mut router = Router::new();
    router.register("echo", 1, ProcedureKind::Query);
    let results = parsed
        .calls
        .iter()
        .map(|call| {
            router
                .route(&call.path, method)
                .map(|_| call.input.clone())
                .map_err(|error| error.clone())
        })
        .collect();
    let _ = build_response(results, parsed.batched);
});
