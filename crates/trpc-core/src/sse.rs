//! Server-Sent Events framing for tRPC v11 subscriptions.
//!
//! The frame layout and event names mirror `@trpc/server` 11.25.0
//! `sseStreamProducer` exactly (verified against its bundled source):
//!
//! * `event: connected` + `data: <client options JSON>` — always first.
//! * `data: <serialized output>` with an optional `id: <eventId>` line for
//!   tracked events; an untracked event emitted after an event id is known
//!   carries an empty `id` line followed by a `restore-id` event that
//!   re-asserts the previous id.
//! * `event: ping` keep-alives, `event: return` for a clean end, and
//!   `event: serialized-error` carrying the flat v11 error shape.
//!
//! Resume: a reconnecting client sends its last event id via the
//! `last-event-id` header, a `lastEventId`/`Last-Event-Id` query parameter,
//! or merged into the subscription input; the adapter passes it to
//! [`SseEncoder::new`]. Ids containing `NUL`, `\n` or `\r` are dropped, which
//! matches the official producer's sanitization.

use crate::{
    error::{ErrorCode, TrpcError},
    json,
};

/// The `connected` frame sent before any data on every subscription stream.
pub const CONNECTED_FRAME: &str = "event: connected\ndata: {}\n\n\n";
/// A `ping` keep-alive frame.
pub const PING_FRAME: &str = "event: ping\ndata: \n\n\n";
/// The `return` frame that ends a stream cleanly.
pub const RETURN_FRAME: &str = "event: return\ndata: \n\n\n";

const SERIALIZED_ERROR_EVENT: &str = "serialized-error";
const RESTORE_ID_EVENT: &str = "restore-id";

fn is_valid_field_text(text: &str) -> bool {
    !text.bytes().any(|byte| matches!(byte, b'\n' | b'\r' | 0))
}

/// Serialize one SSE frame (`event`/`data`/`id` lines followed by a blank line).
///
/// `data` must be single-line (compact JSON qualifies); fields containing a
/// newline, carriage return, or `NUL` are rejected because they would split or
/// inject frames.
///
/// # Errors
/// Returns `INTERNAL_SERVER_ERROR` when a field contains illegal characters.
pub fn encode_frame(
    event: Option<&str>,
    data: &str,
    id: Option<&str>,
) -> Result<String, TrpcError> {
    for (name, value) in [
        ("event", event.unwrap_or_default()),
        ("data", data),
        ("id", id.unwrap_or_default()),
    ] {
        if !is_valid_field_text(value) {
            return Err(TrpcError::new(
                ErrorCode::InternalServerError,
                format!("invalid SSE {name} field"),
            ));
        }
    }
    let mut frame = String::with_capacity(data.len() + 32);
    if let Some(event) = event {
        frame.push_str("event: ");
        frame.push_str(event);
        frame.push('\n');
    }
    frame.push_str("data: ");
    frame.push_str(data);
    frame.push('\n');
    if let Some(id) = id {
        frame.push_str("id: ");
        frame.push_str(id);
        frame.push('\n');
    }
    // The official producer terminates each chunk with "\n\n" after the
    // field newline, yielding a trailing pair of empty lines.
    frame.push_str("\n\n");
    Ok(frame)
}

/// The flat tRPC v11 error object (the `error` member of the error envelope).
#[must_use]
pub fn error_shape(error: &TrpcError) -> json::Value {
    let mut data = error.extra.clone().unwrap_or_default();
    data.insert(
        "code".to_owned(),
        json::Value::String(error.code_name.clone()),
    );
    data.insert(
        "httpStatus".to_owned(),
        json::Value::Number(error.http_status.into()),
    );
    json::json!({
        "message": error.message,
        "code": error.data_code,
        "data": json::Value::Object(data)
    })
}

/// Stateful per-stream SSE encoder mirroring the official producer's id rules.
#[derive(Debug, Default)]
pub struct SseEncoder {
    last_event_id: Option<String>,
}

impl SseEncoder {
    /// Create an encoder; `resume_id` is the client's last-event-id, if any.
    ///
    /// Ids containing `NUL`, `\n`, or `\r` are dropped (the official producer
    /// treats them as absent rather than failing the stream).
    #[must_use]
    pub fn new(resume_id: Option<String>) -> Self {
        Self {
            last_event_id: resume_id.filter(|id| is_valid_field_text(id)),
        }
    }

    /// Encode one yielded value as a data frame.
    ///
    /// `id: Some` marks the event tracked (and advances the resume point);
    /// `id: None` emits an untracked event. When an event id is already known
    /// (from a tracked event or a reconnect `lastEventId`), an untracked event
    /// is sent with an empty `id` line followed by a `restore-id` frame that
    /// re-asserts the last tracked id — exactly the official producer's
    /// behavior, so the client's `lastEventId` is not cleared.
    ///
    /// `data` must be a single-line JSON document produced by the caller.
    ///
    /// # Errors
    /// Returns `INTERNAL_SERVER_ERROR` for field characters that would split
    /// or inject frames.
    pub fn data(&mut self, data: &str, id: Option<&str>) -> Result<String, TrpcError> {
        match id {
            Some(id) => {
                let frame = encode_frame(None, data, Some(id))?;
                self.last_event_id = Some(id.to_owned());
                Ok(frame)
            }
            None => match &self.last_event_id {
                None => encode_frame(None, data, None),
                Some(last) => {
                    let mut out = encode_frame(None, data, Some(""))?;
                    out.push_str(&encode_frame(Some(RESTORE_ID_EVENT), "", Some(last))?);
                    Ok(out)
                }
            },
        }
    }

    /// Encode a `serialized-error` frame carrying the flat v11 error shape.
    ///
    /// # Errors
    /// Returns `INTERNAL_SERVER_ERROR` if serialization fails (unreachable for
    /// the fixed shape) — kept explicit so callers need no `unwrap`.
    pub fn serialized_error(error: &TrpcError) -> Result<String, TrpcError> {
        let data = json::serialize(&error_shape(error)).map_err(|serde_error| {
            TrpcError::new(
                ErrorCode::InternalServerError,
                format!("failed to serialize error shape: {serde_error}"),
            )
        })?;
        encode_frame(Some(SERIALIZED_ERROR_EVENT), &data, None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn control_frames_match_official_layout() {
        assert_eq!(CONNECTED_FRAME, "event: connected\ndata: {}\n\n\n");
        assert_eq!(PING_FRAME, "event: ping\ndata: \n\n\n");
        assert_eq!(RETURN_FRAME, "event: return\ndata: \n\n\n");
    }

    #[test]
    fn data_frame_untracked() {
        let mut encoder = SseEncoder::new(None);
        assert_eq!(
            encoder.data("{\"a\":1}", None).unwrap(),
            "data: {\"a\":1}\n\n\n"
        );
    }

    #[test]
    fn data_frame_tracked_carries_id() {
        let mut encoder = SseEncoder::new(None);
        assert_eq!(
            encoder.data("\"chunk\"", Some("evt-7")).unwrap(),
            "data: \"chunk\"\nid: evt-7\n\n\n"
        );
    }

    #[test]
    fn untracked_after_tracked_restores_id() {
        let mut encoder = SseEncoder::new(None);
        encoder.data("1", Some("9")).unwrap();
        let frame = encoder.data("2", None).unwrap();
        assert_eq!(
            frame,
            "data: 2\nid: \n\n\nevent: restore-id\ndata: \nid: 9\n\n\n"
        );
    }

    #[test]
    fn untracked_after_resume_restores_id() {
        let mut encoder = SseEncoder::new(Some("42".to_owned()));
        let frame = encoder.data("3", None).unwrap();
        assert_eq!(
            frame,
            "data: 3\nid: \n\n\nevent: restore-id\ndata: \nid: 42\n\n\n"
        );
    }

    #[test]
    fn resume_id_with_illegal_characters_is_dropped() {
        let mut encoder = SseEncoder::new(Some("bad\nid".to_owned()));
        assert_eq!(encoder.data("1", None).unwrap(), "data: 1\n\n\n");
    }

    #[test]
    fn data_frame_rejects_multiline_data() {
        let mut encoder = SseEncoder::new(None);
        assert!(encoder.data("line1\nline2", None).is_err());
    }

    #[test]
    fn tracked_id_rejects_injection() {
        let mut encoder = SseEncoder::new(None);
        assert!(encoder.data("1", Some("x\nevent: evil")).is_err());
    }

    #[test]
    fn serialized_error_frame_carries_flat_shape() {
        let error = TrpcError::new(ErrorCode::NotFound, "missing");
        let frame = SseEncoder::serialized_error(&error).unwrap();
        let (head, body) = frame.split_once('\n').unwrap();
        assert_eq!(head, "event: serialized-error");
        let payload: serde_json::Value =
            serde_json::from_str(body.strip_prefix("data: ").unwrap().trim_end()).unwrap();
        assert_eq!(payload["message"], "missing");
        assert_eq!(payload["code"], -32004);
        assert_eq!(payload["data"]["code"], "NOT_FOUND");
        assert_eq!(payload["data"]["httpStatus"], 404);
    }
}
