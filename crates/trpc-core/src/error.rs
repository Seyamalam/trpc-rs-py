//! tRPC error codes and the JSON error envelope.
//!
//! Codes mirror the official tRPC v11 error code table
//! (<https://trpc.io/docs/server/error-handling>); each code maps to the
//! documented HTTP status. Production mode never leaks internal messages:
//! the serialized error shape is fixed and documented in `docs/protocol.md`.

/// Official tRPC v11 error codes with their data codes and HTTP statuses.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorCode {
    /// -32700, HTTP 400. The request body could not be parsed as JSON.
    ParseError,
    /// -33600, HTTP 400. The request was malformed (e.g. invalid batch shape).
    BadRequest,
    /// -32603, HTTP 500. Unexpected server-side failure.
    InternalServerError,
    /// -32004, HTTP 404. Procedure or router path not found.
    NotFound,
    /// -32001, HTTP 401. Authentication required or failed.
    Unauthorized,
    /// -32003, HTTP 403. Insufficient permissions.
    Forbidden,
    /// -32005, HTTP 405. HTTP method not supported for the procedure.
    MethodNotSupported,
    /// -32008, HTTP 408. Procedure timed out.
    Timeout,
    /// -32009, HTTP 409. Conflicting state.
    Conflict,
    /// -32007, HTTP 413. Request payload exceeded the configured limit.
    PayloadTooLarge,
    /// -32012, HTTP 412. A precondition of the request failed.
    ///
    /// Note: tRPC maps this to the same data code as
    /// [`ErrorCode::ClientClosedRequest`] (`-32012`); we mirror that quirk.
    PreconditionFailed,
    /// -32012, HTTP 499. Client disconnected before the response was ready.
    ClientClosedRequest,
}

impl ErrorCode {
    /// Parse an official tRPC error code name.
    #[must_use]
    pub fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "PARSE_ERROR" => Self::ParseError,
            "BAD_REQUEST" => Self::BadRequest,
            "INTERNAL_SERVER_ERROR" => Self::InternalServerError,
            "NOT_FOUND" => Self::NotFound,
            "UNAUTHORIZED" => Self::Unauthorized,
            "FORBIDDEN" => Self::Forbidden,
            "METHOD_NOT_SUPPORTED" => Self::MethodNotSupported,
            "TIMEOUT" => Self::Timeout,
            "CONFLICT" => Self::Conflict,
            "PAYLOAD_TOO_LARGE" => Self::PayloadTooLarge,
            "PRECONDITION_FAILED" => Self::PreconditionFailed,
            "CLIENT_CLOSED_REQUEST" => Self::ClientClosedRequest,
            _ => return None,
        })
    }

    /// The tRPC data code sent on the wire.
    #[must_use]
    pub const fn code(self) -> i32 {
        match self {
            Self::ParseError => -32_700,
            Self::BadRequest => -33_600,
            Self::InternalServerError => -32_603,
            Self::NotFound => -32_004,
            Self::Unauthorized => -32_001,
            Self::Forbidden => -32_003,
            Self::MethodNotSupported => -32_005,
            Self::Timeout => -32_008,
            Self::Conflict => -32_009,
            Self::PayloadTooLarge => -32_007,
            Self::PreconditionFailed | Self::ClientClosedRequest => -32_012,
        }
    }

    /// The HTTP status the error maps to in a direct (non-batched) response.
    #[must_use]
    pub const fn http_status(self) -> u16 {
        match self {
            Self::ParseError | Self::BadRequest => 400,
            Self::InternalServerError => 500,
            Self::NotFound => 404,
            Self::Unauthorized => 401,
            Self::Forbidden => 403,
            Self::MethodNotSupported => 405,
            Self::Timeout => 408,
            Self::Conflict => 409,
            Self::PayloadTooLarge => 413,
            Self::PreconditionFailed => 412,
            Self::ClientClosedRequest => 499,
        }
    }

    /// The `SCREAMING_SNAKE_CASE` name used in the JSON envelope.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::ParseError => "PARSE_ERROR",
            Self::BadRequest => "BAD_REQUEST",
            Self::InternalServerError => "INTERNAL_SERVER_ERROR",
            Self::NotFound => "NOT_FOUND",
            Self::Unauthorized => "UNAUTHORIZED",
            Self::Forbidden => "FORBIDDEN",
            Self::MethodNotSupported => "METHOD_NOT_SUPPORTED",
            Self::Timeout => "TIMEOUT",
            Self::Conflict => "CONFLICT",
            Self::PayloadTooLarge => "PAYLOAD_TOO_LARGE",
            Self::PreconditionFailed => "PRECONDITION_FAILED",
            Self::ClientClosedRequest => "CLIENT_CLOSED_REQUEST",
        }
    }
}

impl std::fmt::Display for ErrorCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.name())
    }
}

/// A single error inside the tRPC error envelope.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrpcError {
    /// Structured error payload; opaque to the wire format.
    pub message: String,
    /// The tRPC data code, e.g. `-32001`.
    pub data_code: i32,
    /// The HTTP status this error maps to.
    pub http_status: u16,
    /// The machine-readable code name, e.g. `"UNAUTHORIZED"`.
    pub code_name: String,
}

impl TrpcError {
    /// Build an error of the given code with a message.
    #[must_use]
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            data_code: code.code(),
            http_status: code.http_status(),
            code_name: code.name().to_owned(),
        }
    }

    /// Serialize to the default tRPC v11 flat error envelope.
    ///
    /// The numeric JSON-RPC code is under `error.code`; the machine-readable
    /// name and HTTP status are under `error.data.code` and
    /// `error.data.httpStatus` respectively.
    #[must_use]
    pub fn to_envelope(&self) -> String {
        // Serialization of serde_json::Value is infallible; this fallback is unreachable.
        crate::json::serialize(&crate::json::error_envelope(self))
            .unwrap_or_else(|_| "null".to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_match_official_table() {
        assert_eq!(ErrorCode::ParseError.code(), -32700);
        assert_eq!(ErrorCode::ParseError.http_status(), 400);
        assert_eq!(ErrorCode::NotFound.code(), -32004);
        assert_eq!(ErrorCode::NotFound.http_status(), 404);
        assert_eq!(ErrorCode::Unauthorized.name(), "UNAUTHORIZED");
    }

    #[test]
    fn from_name_accepts_all_official_names() {
        let codes = [
            ErrorCode::ParseError,
            ErrorCode::BadRequest,
            ErrorCode::InternalServerError,
            ErrorCode::NotFound,
            ErrorCode::Unauthorized,
            ErrorCode::Forbidden,
            ErrorCode::MethodNotSupported,
            ErrorCode::Timeout,
            ErrorCode::Conflict,
            ErrorCode::PayloadTooLarge,
            ErrorCode::PreconditionFailed,
            ErrorCode::ClientClosedRequest,
        ];
        for code in codes {
            assert_eq!(ErrorCode::from_name(code.name()), Some(code));
        }
        assert_eq!(ErrorCode::from_name("NOT_AN_ERROR"), None);
    }

    #[test]
    fn envelope_shape() {
        let err = TrpcError::new(ErrorCode::NotFound, "No procedure found on path \"x\"");
        let json = crate::json::parse(err.to_envelope().as_bytes()).unwrap();
        let inner = &json["error"];
        assert_eq!(inner["message"], "No procedure found on path \"x\"");
        assert_eq!(inner["code"], -32004);
        assert_eq!(inner["data"]["code"], "NOT_FOUND");
        assert_eq!(inner["data"]["httpStatus"], 404);
    }
}
