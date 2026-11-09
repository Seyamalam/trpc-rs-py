//! Internal JSON boundary for the core, isolated per ADR 0002.
//!
//! `serde_json` currently owns parsing/serialization and `Value`. The public
//! `Value` alias exposes `serde_json` types to downstream consumers, so ADR 0002's
//! one-file swap applies only internally, not as a downstream API guarantee.
//! Request parsing necessarily allocates owned procedure paths and JSON values;
//! router lookup itself borrows the caller's path and allocates nothing. Payload
//! bytes are bounded before parsing, and parsed JSON depth is checked with an
//! iterative walk to avoid recursive validation stack growth.

use crate::error::TrpcError;
use serde::Serialize;

/// JSON value used by the protocol API.
pub type Value = serde_json::Value;
#[allow(unused_imports)]
// Macro imports are resolved by path but not counted as ordinary imports.
pub(crate) use serde_json::json;

/// Parse a JSON document while keeping the JSON implementation behind this module.
pub(crate) fn parse(input: &[u8]) -> Result<Value, serde_json::Error> {
    serde_json::from_slice(input)
}

/// Detect `serde_json`'s parser recursion guard, whose error kind is exposed only as syntax.
pub(crate) fn is_recursion_limit_error(error: &serde_json::Error) -> bool {
    // serde_json currently reports this guard with a stable diagnostic string.
    error.to_string().starts_with("recursion limit exceeded")
}

/// Serialize a value as compact JSON.
///
/// Public so the bindings crate serializes event payloads through the same
/// isolated JSON boundary per ADR 0002.
///
/// # Errors
/// Returns the `serde_json` serialization error (e.g. a key that is not a
/// string on a map — impossible for [`Value`], but possible for arbitrary `T`).
pub fn serialize<T: Serialize>(value: &T) -> Result<String, serde_json::Error> {
    serde_json::to_string(value)
}

/// Build the successful tRPC v11 result envelope.
pub(crate) fn result_envelope(output: &Value) -> Value {
    serde_json::json!({"result": {"data": output}})
}

/// Build the default tRPC v11 error envelope.
pub(crate) fn error_envelope(error: &TrpcError) -> Value {
    // The default tRPC v11 error shape is flat under `error`; the `json`
    // transformer wrapper is not part of the error envelope. Typed-error
    // `extra` fields merge into `data` first so the official `code` and
    // `httpStatus` keys always win on collisions — the same rule a Node
    // `errorFormatter` gets from spreading `...shape.data` last.
    let mut data = error.extra.clone().unwrap_or_default();
    data.insert("code".to_owned(), Value::String(error.code_name.clone()));
    data.insert(
        "httpStatus".to_owned(),
        Value::Number(error.http_status.into()),
    );
    serde_json::json!({
        "error": {
            "message": error.message,
            "code": error.data_code,
            "data": Value::Object(data)
        }
    })
}

/// Validate that no JSON container is nested deeper than `max_depth`.
///
/// Scalars have depth zero; each array or object adds one. `serde_json` also
/// applies its own conservative parser recursion guard before this check.
pub(crate) fn validate_depth(value: &Value, max_depth: usize) -> bool {
    let mut pending = vec![(value, 0_usize)];
    while let Some((current, parent_depth)) = pending.pop() {
        let depth = parent_depth.saturating_add(1);
        match current {
            Value::Array(items) => {
                if depth > max_depth {
                    return false;
                }
                pending.extend(items.iter().map(|child| (child, depth)));
            }
            Value::Object(items) => {
                if depth > max_depth {
                    return false;
                }
                pending.extend(items.values().map(|child| (child, depth)));
            }
            _ => {}
        }
    }
    true
}
