//! Shared input-shaping helpers for the `trpc-core` fuzz targets.
//!
//! Each target binary is a separate `libFuzzer` entry point; the helpers here
//! keep the byte-level input layout identical across targets so a seed or a
//! crash artifact means the same thing everywhere. The per-target layouts are
//! documented in `docs/security/fuzzing.md`.

use trpc_core::protocol::Limits;

/// Fuzz inputs larger than this are truncated before shaping.
///
/// The parser's own `max_body_size` (1 MiB default) bounds work anyway; this
/// cap keeps `from_utf8_lossy` and field splitting from allocating on
/// multi-megabyte mutations that the real HTTP layer would reject earlier.
pub const MAX_INPUT: usize = 64 * 1024;

/// Field separator inside a fuzz input (also the record separator in `sse_encode`).
pub const FIELD_SEP: u8 = 0x00;

/// Secondary separator used by `sse_encode` to split a frame record's fields.
pub const SUB_SEP: u8 = 0x01;

/// Truncate `data` to [`MAX_INPUT`].
#[must_use]
pub fn bounded(data: &[u8]) -> &[u8] {
    &data[..data.len().min(MAX_INPUT)]
}

/// Split `data` into at most `count` fields on `sep`.
///
/// The final field keeps the remainder of the input, so one separator never
/// throws bytes away. Missing fields come back as empty slices.
#[must_use]
pub fn fields(data: &[u8], sep: u8, count: usize) -> Vec<&[u8]> {
    let mut parts: Vec<&[u8]> = data.splitn(count, |byte| *byte == sep).collect();
    parts.resize(count, &[]);
    parts
}

/// Interpret `bytes` lossily as UTF-8.
///
/// The HTTP layer hands the parser `&str` for the path and query; lossy
/// conversion lets arbitrary byte sequences through so the parser — not the
/// harness — decides what is malformed. JSON bodies use the raw bytes instead.
#[must_use]
pub fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

/// Derive parser limits from fuzz bytes.
///
/// Layout: byte 0 selects the mode — even means [`Limits::default`], odd means
/// bytes 1..=3 drive `max_body_size` (`byte * 64`), `max_batch_size`, and
/// `max_json_depth` directly. Roughly half of all runs therefore exercise the
/// production defaults while the rest probe the limit-enforcement branches
/// (zero depth, zero batch, tiny bodies).
#[must_use]
pub fn limits(bytes: &[u8]) -> Limits {
    let at = |index: usize| usize::from(*bytes.get(index).unwrap_or(&0));
    if at(0) & 1 == 0 {
        return Limits::default();
    }
    Limits {
        max_body_size: at(1).saturating_mul(64),
        max_batch_size: at(2),
        max_json_depth: at(3),
    }
}
