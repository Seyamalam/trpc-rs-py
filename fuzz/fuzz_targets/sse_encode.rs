//! Fuzzes the SSE encode paths whose fields carry input-derived text:
//! reconnect `lastEventId`s (untrusted client header), tracked event ids, and
//! the raw `encode_frame` fields — anywhere a newline/NUL could split or
//! inject a frame. Also feeds fuzzed text through `serialized_error` (a
//! handler message reaches the frame data).
//!
//! Input layout:
//!   field 0          reconnect id for `SseEncoder::new` (empty → `None`)
//!   field 1..        frame records, each `[flag][data]\x01[id]`:
//!                    flag bit 0 → call `data()` with a tracked id,
//!                    flag bit 1 → also feed record fields to `encode_frame`,
//!                    flag bit 2 → build a `serialized-error` frame whose
//!                    message is the record's `data` field
//!   fields are separated by `\x00`, record fields by `\x01`

#![no_main]

use libfuzzer_sys::fuzz_target;
use trpc_core::error::{ErrorCode, TrpcError};
use trpc_core::sse::{SseEncoder, encode_frame};
use trpc_core_fuzz::{FIELD_SEP, SUB_SEP, bounded, fields, text};

fuzz_target!(|data: &[u8]| {
    let data = bounded(data);
    let parts = fields(data, FIELD_SEP, 64);
    let mut iter = parts.into_iter();
    let resume = iter.next().unwrap_or_default();
    let mut encoder = SseEncoder::new((!resume.is_empty()).then(|| text(resume)));

    for record in iter {
        let flag = record.first().copied().unwrap_or(0);
        let kv = fields(record.get(1..).unwrap_or_default(), SUB_SEP, 2);
        let payload = text(kv[0]);
        let id = text(kv[1]);

        let tracked = (flag & 1 == 1).then_some(id.as_str());
        let _ = encoder.data(&payload, tracked);

        if flag & 2 == 2 {
            let _ = encode_frame(Some(payload.as_str()), id.as_str(), tracked);
        }
        if flag & 4 == 4 {
            let error = TrpcError::new(ErrorCode::NotFound, payload.clone());
            let _ = SseEncoder::serialized_error(&error);
        }
    }
});
