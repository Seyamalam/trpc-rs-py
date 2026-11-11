//! Fuzzes the tRPC POST-body parse path: declared `Content-Length` versus the
//! actual body, JSON parsing and depth limits, the `batch=1` flag from the
//! query string, and routing/envelope construction on the parsed calls.
//!
//! Input layout (all offsets in bytes):
//!   [0..4]   limit knobs (see `trpc_core_fuzz::limits`)
//!   [4]      content-length mode:
//!              0        → `None`
//!              1        → `Some(body.len())` (honest adapter)
//!              else     → `Some(byte * 1024)` (lying adapter)
//!   [5..]    path `\x00` query `\x00` body — split on the first two NULs

#![no_main]

use libfuzzer_sys::fuzz_target;
use trpc_core::protocol::{
    HttpMethod, ProcedureKind, Request, Router, build_response, parse_request,
};
use trpc_core_fuzz::{FIELD_SEP, bounded, fields, limits, text};

fuzz_target!(|data: &[u8]| {
    let data = bounded(data);
    let limits = limits(&data[..4.min(data.len())]);
    let Some(&mode) = data.get(4) else { return };
    let Some(rest) = data.get(5..) else { return };
    let parts = fields(rest, FIELD_SEP, 3);
    let path = text(parts[0]);
    let query = text(parts[1]);
    let body = parts[2];

    let content_length = match mode {
        0 => None,
        1 => Some(body.len()),
        byte => Some(usize::from(byte) * 1024),
    };
    let request = Request {
        method: HttpMethod::Post,
        path: &path,
        query: &query,
        body,
        content_length,
    };
    let Ok(parsed) = parse_request(request, limits) else {
        return;
    };

    let mut router = Router::new();
    router.register("echo", 1, ProcedureKind::Mutation);
    let results = parsed
        .calls
        .iter()
        .map(|call| {
            router
                .route(&call.path, HttpMethod::Post)
                .map(|_| call.input.clone())
                .map_err(|error| error.clone())
        })
        .collect();
    let _ = build_response(results, parsed.batched);
});
