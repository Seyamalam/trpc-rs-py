//! Internal JSON boundary for the core, isolated per ADR 0002.
//!
//! `serde_json` currently owns parsing/serialization and `Value`. Request parsing
//! necessarily allocates owned procedure paths and JSON values; router lookup
//! itself borrows the caller's path and allocates nothing. Payload bytes are
//! bounded before parsing, and parsed JSON depth is checked with an iterative
//! walk to avoid recursive validation stack growth.

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

/// Serialize a value as compact JSON.
pub(crate) fn serialize<T: Serialize>(value: &T) -> Result<String, serde_json::Error> {
    serde_json::to_string(value)
}

/// Build the successful tRPC v11 result envelope.
pub(crate) fn result_envelope(output: &Value) -> Value {
    serde_json::json!({"result": {"data": output}})
}

/// Build the tRPC error envelope.
pub(crate) fn error_envelope(error: &TrpcError) -> Value {
    // VERIFY(compat): the task's required wire shape puts httpStatus directly
    // under error.json; compare this placement with official @trpc/client tests.
    serde_json::json!({
        "error": {
            "json": {
                "message": error.message,
                "code": error.data_code,
                "httpStatus": error.http_status,
                "data": {
                    "code": error.code_name,
                    "httpStatus": error.http_status
                }
            }
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
