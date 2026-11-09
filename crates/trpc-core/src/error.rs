//! tRPC error codes and the JSON error envelope.
//!
//! Codes mirror the official tRPC v11 error code table
//! (<https://trpc.io/docs/server/error-handling>); each code maps to the
//! documented HTTP status. Production mode never leaks internal messages:
//! the serialized error shape is fixed and documented in `docs/protocol.md`.

/// Official tRPC v11 error codes with their data codes and HTTP statuses.
///
/// The table mirrors `TRPC_ERROR_CODES_BY_KEY` and
/// `getHTTPStatusCodeFromError` in `@trpc/server` v11, verified against
/// `@trpc/server@11.25.0`. Several codes share the `-32603` data code
/// (JSON-RPC internal-error range) but carry distinct HTTP statuses.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorCode {
    /// -32700, HTTP 400. The request body could not be parsed as JSON.
    ParseError,
    /// -32600, HTTP 400. The request was malformed (e.g. invalid batch shape).
    BadRequest,
    /// -32603, HTTP 500. Unexpected server-side failure.
    InternalServerError,
    /// -32603, HTTP 501. The procedure is not implemented.
    NotImplemented,
    /// -32603, HTTP 502. An upstream call returned an invalid response.
    BadGateway,
    /// -32603, HTTP 503. The service is temporarily unavailable.
    ServiceUnavailable,
    /// -32603, HTTP 504. An upstream call timed out.
    GatewayTimeout,
    /// -32001, HTTP 401. Authentication required or failed.
    Unauthorized,
    /// -32002, HTTP 402. Payment required.
    PaymentRequired,
    /// -32003, HTTP 403. Insufficient permissions.
    Forbidden,
    /// -32004, HTTP 404. Procedure or router path not found.
    NotFound,
    /// -32005, HTTP 405. HTTP method not supported for the procedure.
    MethodNotSupported,
    /// -32008, HTTP 408. Procedure timed out.
    Timeout,
    /// -32009, HTTP 409. Conflicting state.
    Conflict,
    /// -32012, HTTP 412. A precondition of the request failed.
    PreconditionFailed,
    /// -32013, HTTP 413. Request payload exceeded the configured limit.
    PayloadTooLarge,
    /// -32015, HTTP 415. The request content type is unsupported.
    UnsupportedMediaType,
    /// -32022, HTTP 422. The request was well-formed but unprocessable.
    UnprocessableContent,
    /// -32028, HTTP 428. A precondition is required before retrying.
    PreconditionRequired,
    /// -32029, HTTP 429. The caller was rate limited.
    TooManyRequests,
    /// -32099, HTTP 499. Client disconnected before the response was ready.
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
            "NOT_IMPLEMENTED" => Self::NotImplemented,
            "BAD_GATEWAY" => Self::BadGateway,
            "SERVICE_UNAVAILABLE" => Self::ServiceUnavailable,
            "GATEWAY_TIMEOUT" => Self::GatewayTimeout,
            "UNAUTHORIZED" => Self::Unauthorized,
            "PAYMENT_REQUIRED" => Self::PaymentRequired,
            "FORBIDDEN" => Self::Forbidden,
            "NOT_FOUND" => Self::NotFound,
            "METHOD_NOT_SUPPORTED" => Self::MethodNotSupported,
            "TIMEOUT" => Self::Timeout,
            "CONFLICT" => Self::Conflict,
            "PRECONDITION_FAILED" => Self::PreconditionFailed,
            "PAYLOAD_TOO_LARGE" => Self::PayloadTooLarge,
            "UNSUPPORTED_MEDIA_TYPE" => Self::UnsupportedMediaType,
            "UNPROCESSABLE_CONTENT" => Self::UnprocessableContent,
            "PRECONDITION_REQUIRED" => Self::PreconditionRequired,
            "TOO_MANY_REQUESTS" => Self::TooManyRequests,
            "CLIENT_CLOSED_REQUEST" => Self::ClientClosedRequest,
            _ => return None,
        })
    }

    /// The tRPC data code sent on the wire.
    #[must_use]
    pub const fn code(self) -> i32 {
        match self {
            Self::ParseError => -32_700,
            Self::BadRequest => -32_600,
            Self::InternalServerError
            | Self::NotImplemented
            | Self::BadGateway
            | Self::ServiceUnavailable
            | Self::GatewayTimeout => -32_603,
            Self::Unauthorized => -32_001,
            Self::PaymentRequired => -32_002,
            Self::Forbidden => -32_003,
            Self::NotFound => -32_004,
            Self::MethodNotSupported => -32_005,
            Self::Timeout => -32_008,
            Self::Conflict => -32_009,
            Self::PreconditionFailed => -32_012,
            Self::PayloadTooLarge => -32_013,
            Self::UnsupportedMediaType => -32_015,
            Self::UnprocessableContent => -32_022,
            Self::PreconditionRequired => -32_028,
            Self::TooManyRequests => -32_029,
            Self::ClientClosedRequest => -32_099,
        }
    }

    /// The HTTP status the error maps to in a direct (non-batched) response.
    #[must_use]
    pub const fn http_status(self) -> u16 {
        match self {
            Self::ParseError | Self::BadRequest => 400,
            Self::InternalServerError => 500,
            Self::NotImplemented => 501,
            Self::BadGateway => 502,
            Self::ServiceUnavailable => 503,
            Self::GatewayTimeout => 504,
            Self::Unauthorized => 401,
            Self::PaymentRequired => 402,
            Self::Forbidden => 403,
            Self::NotFound => 404,
            Self::MethodNotSupported => 405,
            Self::Timeout => 408,
            Self::Conflict => 409,
            Self::PreconditionFailed => 412,
            Self::PayloadTooLarge => 413,
            Self::UnsupportedMediaType => 415,
            Self::UnprocessableContent => 422,
            Self::PreconditionRequired => 428,
            Self::TooManyRequests => 429,
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
            Self::NotImplemented => "NOT_IMPLEMENTED",
            Self::BadGateway => "BAD_GATEWAY",
            Self::ServiceUnavailable => "SERVICE_UNAVAILABLE",
            Self::GatewayTimeout => "GATEWAY_TIMEOUT",
            Self::Unauthorized => "UNAUTHORIZED",
            Self::PaymentRequired => "PAYMENT_REQUIRED",
            Self::Forbidden => "FORBIDDEN",
            Self::NotFound => "NOT_FOUND",
            Self::MethodNotSupported => "METHOD_NOT_SUPPORTED",
            Self::Timeout => "TIMEOUT",
            Self::Conflict => "CONFLICT",
            Self::PreconditionFailed => "PRECONDITION_FAILED",
            Self::PayloadTooLarge => "PAYLOAD_TOO_LARGE",
            Self::UnsupportedMediaType => "UNSUPPORTED_MEDIA_TYPE",
            Self::UnprocessableContent => "UNPROCESSABLE_CONTENT",
            Self::PreconditionRequired => "PRECONDITION_REQUIRED",
            Self::TooManyRequests => "TOO_MANY_REQUESTS",
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
#[derive(Debug, Clone, PartialEq)]
pub struct TrpcError {
    /// Structured error payload; opaque to the wire format.
    pub message: String,
    /// The tRPC data code, e.g. `-32001`.
    pub data_code: i32,
    /// The HTTP status this error maps to.
    pub http_status: u16,
    /// The machine-readable code name, e.g. `"UNAUTHORIZED"`.
    pub code_name: String,
    /// Extra structured fields merged into `error.data` for typed errors.
    ///
    /// Mirrors what a Node `errorFormatter` produces when it returns
    /// `{ ...shape.data, customField }`: the entries sit inside
    /// `error.data` alongside `code` and `httpStatus`. Reserved keys
    /// (`code`, `httpStatus`) cannot be overridden.
    pub extra: Option<serde_json::Map<String, serde_json::Value>>,
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
            extra: None,
        }
    }

    /// Attach extra fields merged into `error.data` on the wire.
    #[must_use]
    pub fn with_extra(mut self, extra: serde_json::Map<String, serde_json::Value>) -> Self {
        self.extra = Some(extra);
        self
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

    /// `(code, data_code, http_status)` mirroring
    /// `TRPC_ERROR_CODES_BY_KEY` + `getHTTPStatusCodeFromError` in
    /// `@trpc/server@11.25.0`.
    const OFFICIAL: &[(ErrorCode, i32, u16)] = &[
        (ErrorCode::ParseError, -32700, 400),
        (ErrorCode::BadRequest, -32600, 400),
        (ErrorCode::InternalServerError, -32603, 500),
        (ErrorCode::NotImplemented, -32603, 501),
        (ErrorCode::BadGateway, -32603, 502),
        (ErrorCode::ServiceUnavailable, -32603, 503),
        (ErrorCode::GatewayTimeout, -32603, 504),
        (ErrorCode::Unauthorized, -32001, 401),
        (ErrorCode::PaymentRequired, -32002, 402),
        (ErrorCode::Forbidden, -32003, 403),
        (ErrorCode::NotFound, -32004, 404),
        (ErrorCode::MethodNotSupported, -32005, 405),
        (ErrorCode::Timeout, -32008, 408),
        (ErrorCode::Conflict, -32009, 409),
        (ErrorCode::PreconditionFailed, -32012, 412),
        (ErrorCode::PayloadTooLarge, -32013, 413),
        (ErrorCode::UnsupportedMediaType, -32015, 415),
        (ErrorCode::UnprocessableContent, -32022, 422),
        (ErrorCode::PreconditionRequired, -32028, 428),
        (ErrorCode::TooManyRequests, -32029, 429),
        (ErrorCode::ClientClosedRequest, -32099, 499),
    ];

    #[test]
    fn codes_match_official_table() {
        assert_eq!(OFFICIAL.len(), 21);
        for &(code, data_code, status) in OFFICIAL {
            assert_eq!(code.code(), data_code, "data code for {}", code.name());
            assert_eq!(code.http_status(), status, "status for {}", code.name());
        }
        assert_eq!(ErrorCode::Unauthorized.name(), "UNAUTHORIZED");
    }

    #[test]
    fn from_name_accepts_all_official_names() {
        for &(code, _, _) in OFFICIAL {
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

    #[test]
    fn envelope_merges_extra_fields_but_official_keys_win() {
        let mut extra = serde_json::Map::new();
        extra.insert("post_id".to_owned(), 5.into());
        extra.insert("code".to_owned(), "SPOOFED".into());
        extra.insert("httpStatus".to_owned(), 200.into());
        let err = TrpcError::new(ErrorCode::NotFound, "missing").with_extra(extra);
        let json = crate::json::parse(err.to_envelope().as_bytes()).unwrap();
        let data = &json["error"]["data"];
        assert_eq!(data["post_id"], 5);
        assert_eq!(data["code"], "NOT_FOUND");
        assert_eq!(data["httpStatus"], 404);
    }
}
