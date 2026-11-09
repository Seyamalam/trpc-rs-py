//! Criterion micro-benchmarks for the protocol hot paths (MISSION.md §10).
//!
//! Run with `cargo bench -p trpc-core --bench protocol`; `benchmarks/run.py`
//! collects `target/criterion/*/new/estimates.json` into the results JSON.

use std::fmt::Write as _;

use criterion::{BatchSize, Criterion, black_box, criterion_group, criterion_main};
use serde_json::json;
use trpc_core::error::{ErrorCode, TrpcError};
use trpc_core::protocol::{
    HttpMethod, Limits, ProcedureKind, Request, Router, build_response, parse_request,
};

const SINGLE_BODY: &[u8] = br#"{"message":"hello"}"#;

fn percent_encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() {
            out.push(char::from(byte));
        } else {
            let _ = write!(out, "%{byte:02X}");
        }
    }
    out
}

fn batch_body(count: usize) -> String {
    let entries: Vec<String> = (0..count)
        .map(|index| format!(r#""{index}":{{"name":"ada"}}"#))
        .collect();
    format!("{{{}}}", entries.join(","))
}

fn batch_paths(count: usize) -> String {
    (0..count)
        .map(|index| format!("greet{index}"))
        .collect::<Vec<_>>()
        .join(",")
}

fn bench_parse(c: &mut Criterion) {
    let limits = Limits::default();
    let single_query = format!("input={}", percent_encode(r#"{"name":"ada"}"#));
    let paths10 = batch_paths(10);
    let body10 = batch_body(10);
    let batch_query = format!("batch=1&input={}", percent_encode(&body10));

    c.bench_function("parse_single_get", |b| {
        b.iter(|| {
            parse_request(
                black_box(Request {
                    method: HttpMethod::Get,
                    path: "greet",
                    query: &single_query,
                    body: b"",
                    content_length: None,
                }),
                black_box(limits),
            )
            .unwrap()
        });
    });
    c.bench_function("parse_single_post", |b| {
        b.iter(|| {
            parse_request(
                black_box(Request {
                    method: HttpMethod::Post,
                    path: "echo",
                    query: "",
                    body: SINGLE_BODY,
                    content_length: Some(SINGLE_BODY.len()),
                }),
                black_box(limits),
            )
            .unwrap()
        });
    });
    c.bench_function("parse_batch_10_get", |b| {
        b.iter(|| {
            parse_request(
                black_box(Request {
                    method: HttpMethod::Get,
                    path: &paths10,
                    query: &batch_query,
                    body: b"",
                    content_length: None,
                }),
                black_box(limits),
            )
            .unwrap()
        });
    });
    c.bench_function("parse_batch_10_post", |b| {
        b.iter(|| {
            parse_request(
                black_box(Request {
                    method: HttpMethod::Post,
                    path: &paths10,
                    query: "batch=1",
                    body: body10.as_bytes(),
                    content_length: Some(body10.len()),
                }),
                black_box(limits),
            )
            .unwrap()
        });
    });
}

fn bench_route(c: &mut Criterion) {
    let mut router = Router::new();
    for index in 0..200u64 {
        router.register(format!("ns.proc{index}"), index, ProcedureKind::Query);
    }
    c.bench_function("route_lookup_200", |b| {
        b.iter(|| {
            router
                .route(black_box("ns.proc199"), black_box(HttpMethod::Get))
                .unwrap()
        });
    });
}

fn bench_respond(c: &mut Criterion) {
    let value = json!({"greeting": "Hello ada"});
    let error = TrpcError::new(ErrorCode::NotFound, "No procedure found on path \"x\"");
    c.bench_function("build_response_single", |b| {
        b.iter_batched(
            || value.clone(),
            |output| build_response(vec![Ok(output)], black_box(false)),
            BatchSize::SmallInput,
        );
    });
    c.bench_function("build_response_batch_10", |b| {
        b.iter_batched(
            || {
                std::iter::repeat_with(|| value.clone())
                    .take(10)
                    .collect::<Vec<_>>()
            },
            |outputs| build_response(outputs.into_iter().map(Ok).collect(), black_box(true)),
            BatchSize::SmallInput,
        );
    });
    c.bench_function("error_envelope", |b| {
        b.iter(|| black_box(&error).to_envelope());
    });
}

criterion_group!(benches, bench_parse, bench_route, bench_respond);
criterion_main!(benches);
