//! tRPC v11 HTTP request parsing and response envelope construction.
//!
//! The parser performs one pass over the batch input object and owns decoded
//! paths/JSON values. Body length is checked before JSON parsing; the JSON depth
//! check is a bounded iterative walk after `serde_json`'s parser recursion guard.

use std::collections::HashMap;

use crate::{
    error::{ErrorCode, TrpcError},
    json,
};

/// Default maximum request JSON size (1 MiB).
pub const DEFAULT_MAX_BODY_SIZE: usize = 1024 * 1024;
/// Default maximum number of calls in one batch.
pub const DEFAULT_MAX_BATCH_SIZE: usize = 100;
/// Default maximum nested JSON containers.
pub const DEFAULT_MAX_JSON_DEPTH: usize = 32;

/// Configurable request parsing limits.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    /// Maximum input size in bytes, including GET's encoded `input` value.
    pub max_body_size: usize,
    /// Maximum number of procedures in one batch.
    pub max_batch_size: usize,
    /// Maximum nesting depth of any JSON input value.
    pub max_json_depth: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_body_size: DEFAULT_MAX_BODY_SIZE,
            max_batch_size: DEFAULT_MAX_BATCH_SIZE,
            max_json_depth: DEFAULT_MAX_JSON_DEPTH,
        }
    }
}

/// HTTP method supported by a parsed tRPC request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HttpMethod {
    /// Read-only query request.
    Get,
    /// Mutation request.
    Post,
}

/// Procedure behavior used to enforce HTTP method restrictions.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcedureKind {
    /// Side-effect-free query.
    Query,
    /// State-changing mutation.
    Mutation,
    /// Server-sent event stream; GET-only like a query but answered as SSE.
    Subscription,
}

/// One procedure call after parsing the HTTP request.
#[derive(Debug, Clone, PartialEq)]
pub struct ProcedureCall {
    /// Procedure path, such as `post.byId`.
    pub path: String,
    /// Decoded raw JSON input; missing input is represented as JSON null.
    pub input: json::Value,
}

/// A parsed tRPC HTTP request.
#[derive(Debug, Clone, PartialEq)]
pub struct ParsedRequest {
    /// Calls in request order.
    pub calls: Vec<ProcedureCall>,
    /// Whether this was a batched request.
    pub batched: bool,
}

/// Request components supplied by an HTTP adapter.
#[derive(Debug, Clone, Copy)]
pub struct Request<'a> {
    /// HTTP method.
    pub method: HttpMethod,
    /// URL path segment after the tRPC endpoint prefix.
    pub path: &'a str,
    /// Raw query string without the leading `?`.
    pub query: &'a str,
    /// Request body bytes (empty for GET).
    pub body: &'a [u8],
    /// Optional Content-Length, checked before parsing when provided.
    pub content_length: Option<usize>,
}

fn protocol_error(code: ErrorCode, message: &'static str) -> TrpcError {
    TrpcError::new(code, message)
}

fn bad_request(message: &'static str) -> TrpcError {
    protocol_error(ErrorCode::BadRequest, message)
}

fn decode_percent(input: &str) -> Result<Vec<u8>, TrpcError> {
    let bytes = input.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                decoded.push(b' ');
                index += 1;
            }
            b'%' => {
                if index + 2 >= bytes.len() {
                    return Err(bad_request("Malformed percent-encoding in query string"));
                }
                let high = hex(bytes[index + 1]);
                let low = hex(bytes[index + 2]);
                match (high, low) {
                    (Some(high), Some(low)) => decoded.push((high << 4) | low),
                    _ => return Err(bad_request("Malformed percent-encoding in query string")),
                }
                index += 3;
            }
            byte => {
                decoded.push(byte);
                index += 1;
            }
        }
    }
    Ok(decoded)
}

fn hex(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn query_param<'a>(query: &'a str, name: &str) -> Option<&'a str> {
    query.split('&').find_map(|part| {
        let (key, value) = part.split_once('=')?;
        (key == name).then_some(value)
    })
}

/// The effective cap is the minimum of `max_json_depth` and the parser guard in `serde_json`.
fn input_value(input: &[u8], max_depth: usize) -> Result<json::Value, TrpcError> {
    let value = json::parse(input).map_err(|error| {
        if json::is_recursion_limit_error(&error) {
            protocol_error(ErrorCode::BadRequest, "JSON nesting depth exceeded")
        } else {
            protocol_error(ErrorCode::ParseError, "Invalid JSON input")
        }
    })?;
    if !json::validate_depth(&value, max_depth) {
        return Err(protocol_error(
            ErrorCode::BadRequest,
            "JSON nesting depth exceeded",
        ));
    }
    Ok(value)
}

/// Parse a tRPC v11 query over GET or POST, or a mutation over POST.
///
/// Queries are accepted over GET or POST (including official client method
/// overrides); mutations are accepted only over POST.
///
/// # Errors
/// Returns a `TrpcError` for malformed requests or exceeded limits.
pub fn parse_request(request: Request<'_>, limits: Limits) -> Result<ParsedRequest, TrpcError> {
    if request
        .content_length
        .is_some_and(|length| length > limits.max_body_size)
        || request.body.len() > limits.max_body_size
    {
        return Err(protocol_error(
            ErrorCode::PayloadTooLarge,
            "Request body is too large",
        ));
    }
    let batch_flag = query_param(request.query, "batch").is_some_and(|value| value == "1");
    let path_count = request.path.split(',').count();
    if request.path.is_empty() {
        return Err(bad_request("Procedure path is empty"));
    }
    if batch_flag && path_count > limits.max_batch_size {
        return Err(protocol_error(
            ErrorCode::PayloadTooLarge,
            "Batch size limit exceeded",
        ));
    }
    let batched = batch_flag;
    if !batched && path_count != 1 {
        return Err(bad_request("Batch flag and procedure paths do not match"));
    }
    let paths: Vec<&str> = request.path.split(',').collect();
    if paths.iter().any(|path| path.is_empty()) {
        return Err(bad_request("Procedure path is empty"));
    }
    if paths.iter().any(|path| {
        !path
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
    }) {
        return Err(bad_request("Invalid procedure path"));
    }

    let input = match request.method {
        HttpMethod::Get => {
            if !request.body.is_empty() {
                return Err(bad_request("GET requests must not include a body"));
            }
            let raw = query_param(request.query, "input");
            match raw {
                Some(encoded) => {
                    if encoded.len() > limits.max_body_size {
                        return Err(protocol_error(
                            ErrorCode::PayloadTooLarge,
                            "Request input is too large",
                        ));
                    }
                    let decoded = decode_percent(encoded)?;
                    input_value(&decoded, limits.max_json_depth)?
                }
                None => json::Value::Null,
            }
        }
        HttpMethod::Post => {
            if request.body.is_empty() {
                if batched {
                    // VERIFY(compat): official clients always send a batch body; reject empty batches.
                    return Err(bad_request("Batched POST requests require an input body"));
                }
                // The official client omits the body for an undefined single-call input.
                json::Value::Null
            } else {
                input_value(request.body, limits.max_json_depth)?
            }
        }
    };

    let inputs = if batched {
        batch_inputs(input, paths.len())?
    } else {
        vec![input]
    };

    let calls = paths
        .into_iter()
        .zip(inputs)
        .map(|(path, input)| ProcedureCall {
            path: path.to_owned(),
            input,
        })
        .collect();
    Ok(ParsedRequest { calls, batched })
}

fn batch_inputs(value: json::Value, count: usize) -> Result<Vec<json::Value>, TrpcError> {
    if value == json::Value::Object(serde_json::Map::new()) {
        // The official client omits every undefined input while JSON-stringifying
        // its batch dictionary, yielding `{}` for an all-no-input batch.
        return Ok(vec![json::Value::Null; count]);
    }
    let json::Value::Object(mut object) = value else {
        return Err(bad_request("Batch input must be a JSON object"));
    };
    // The official client omits `undefined` inputs when JSON-stringifying the
    // batch dictionary. Missing indexes therefore mean no input (null) for
    // that call; present indexes must still be numeric and within this batch.
    for key in object.keys() {
        let Ok(index) = key.parse::<usize>() else {
            return Err(bad_request("Batch input keys must be numeric indexes"));
        };
        if index >= count {
            return Err(bad_request("Batch input index exceeds procedure paths"));
        }
    }
    let mut inputs = Vec::with_capacity(count);
    for index in 0..count {
        let key = index.to_string();
        inputs.push(object.remove(&key).unwrap_or(json::Value::Null));
    }
    Ok(inputs)
}

/// Registered procedure identifier and behavior.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Procedure {
    /// Application-defined procedure identifier.
    pub id: u64,
    /// Query or mutation semantics.
    pub kind: ProcedureKind,
}

/// Borrowed-path procedure table; lookup does not allocate.
#[derive(Debug, Clone, Default)]
pub struct Router {
    procedures: HashMap<String, Procedure>,
}

impl Router {
    /// Create an empty router.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a procedure; path allocation happens only during router setup.
    pub fn register(
        &mut self,
        path: impl Into<String>,
        id: u64,
        kind: ProcedureKind,
    ) -> Option<Procedure> {
        self.procedures.insert(path.into(), Procedure { id, kind })
    }

    /// Look up a procedure without allocating.
    #[must_use]
    pub fn lookup(&self, path: &str) -> Option<&Procedure> {
        self.procedures.get(path)
    }

    /// Merge another router. Later entries replace procedures with duplicate paths.
    pub fn merge(&mut self, other: Self) {
        self.procedures.extend(other.procedures);
    }

    /// Add another router under a dotted namespace, allocating names during setup only.
    pub fn nest(&mut self, prefix: &str, other: &Self) {
        for (path, procedure) in &other.procedures {
            self.procedures
                .insert(format!("{prefix}.{path}"), *procedure);
        }
    }

    /// Route a parsed call and verify its HTTP method against registered procedure kind.
    ///
    /// # Errors
    /// Returns not-found or method-not-supported errors.
    pub fn route(&self, path: &str, method: HttpMethod) -> Result<&Procedure, TrpcError> {
        let procedure = self.lookup(path).ok_or_else(|| {
            TrpcError::new(
                ErrorCode::NotFound,
                format!("No procedure found on path \"{path}\""),
            )
        })?;
        // Mutations remain POST-only; queries also accept POST for client method
        // overrides; subscriptions are GET-only like the official method table.
        let allowed = matches!(
            (method, procedure.kind),
            (_, ProcedureKind::Query)
                | (HttpMethod::Post, ProcedureKind::Mutation)
                | (HttpMethod::Get, ProcedureKind::Subscription)
        );
        if !allowed {
            return Err(TrpcError::new(
                ErrorCode::MethodNotSupported,
                "HTTP method is not supported for this procedure",
            ));
        }
        Ok(procedure)
    }
}

/// Complete HTTP response body and status for one call or a batch.
#[derive(Debug, Clone, PartialEq)]
pub struct Response {
    /// HTTP status selected for the entire response.
    pub http_status: u16,
    /// Compact JSON response body.
    pub body: String,
}

/// Construct single or batched tRPC result/error envelopes.
///
/// For batches this chooses 200 on all-success, 500 if any error is 5xx, and
/// otherwise the first error's status. This is a deliberate policy pending
/// compatibility confirmation against the official v11 client.
#[must_use]
pub fn build_response(results: Vec<Result<json::Value, TrpcError>>, batched: bool) -> Response {
    if !batched {
        let result = results
            .into_iter()
            .next()
            .unwrap_or_else(|| Err(bad_request("No procedure result")));
        let (status, envelope) = match result {
            Ok(output) => (200, json::result_envelope(&output)),
            Err(error) => (error.http_status, json::error_envelope(&error)),
        };
        return Response {
            http_status: status,
            // Serialization of a serde_json::Value is infallible; this fallback is unreachable.
            body: json::serialize(&envelope).unwrap_or_else(|_| "null".to_owned()),
        };
    }
    let mut status = 200;
    let mut envelopes = Vec::with_capacity(results.len());
    for result in results {
        match result {
            Ok(output) => envelopes.push(json::result_envelope(&output)),
            Err(error) => {
                if status == 200 {
                    status = error.http_status;
                }
                if error.http_status >= 500 {
                    status = 500;
                }
                envelopes.push(json::error_envelope(&error));
            }
        }
    }
    // VERIFY(compat): tRPC batch status behavior is selected by the explicit
    // project rule above; confirm against the official @trpc/client in compat tests.
    Response {
        http_status: status,
        // Serialization of serde_json::Value is infallible; this fallback is unreachable.
        body: json::serialize(&envelopes).unwrap_or_else(|_| "[]".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn get<'a>(path: &'a str, query: &'a str) -> Request<'a> {
        Request {
            method: HttpMethod::Get,
            path,
            query,
            body: b"",
            content_length: None,
        }
    }

    fn post<'a>(path: &'a str, query: &'a str, body: &'a [u8]) -> Request<'a> {
        Request {
            method: HttpMethod::Post,
            path,
            query,
            body,
            content_length: Some(body.len()),
        }
    }

    #[test]
    fn validates_procedure_path_charset() {
        assert_eq!(
            parse_request(get("post.hello world", ""), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
        assert_eq!(
            parse_request(get("post.你好", ""), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
        assert_eq!(
            parse_request(get("post.by-id_2", ""), Limits::default())
                .unwrap()
                .calls[0]
                .path,
            "post.by-id_2"
        );
    }

    #[test]
    fn parses_single_get_query() {
        let parsed = parse_request(
            get("post.list", "input=%7B%22limit%22%3A2%7D"),
            Limits::default(),
        )
        .unwrap();
        assert_eq!(parsed.calls[0].path, "post.list");
        assert_eq!(parsed.calls[0].input["limit"], 2);
        assert!(!parsed.batched);
    }

    #[test]
    fn parses_batched_get_query_in_order() {
        let parsed = parse_request(
            get(
                "post.list,post.byId",
                "batch=1&input=%7B%220%22%3A%7B%22n%22%3A1%7D%2C%221%22%3A2%7D",
            ),
            Limits::default(),
        )
        .unwrap();
        assert!(parsed.batched);
        assert_eq!(parsed.calls[0].input["n"], 1);
        assert_eq!(parsed.calls[1].input, 2);
    }

    #[test]
    fn parses_single_post_mutation() {
        let parsed = parse_request(
            post("post.create", "", br#"{"title":"x"}"#),
            Limits::default(),
        )
        .unwrap();
        assert_eq!(parsed.calls[0].input["title"], "x");
    }

    #[test]
    fn parses_batched_post_mutation() {
        let parsed = parse_request(
            post(
                "post.create,post.delete",
                "batch=1",
                br#"{"0":{"title":"x"},"1":{"id":2}}"#,
            ),
            Limits::default(),
        )
        .unwrap();
        assert_eq!(parsed.calls[1].input["id"], 2);
    }

    #[test]
    fn rejects_batched_post_non_sequential_input_keys() {
        let error = parse_request(
            post("first,second", "batch=1", br#"{"1":1,"2":2}"#),
            Limits::default(),
        )
        .unwrap_err();
        assert_eq!(error.code_name, "BAD_REQUEST");
    }

    #[test]
    fn enforces_depth_limit_for_batched_get_input() {
        let error = parse_request(
            get(
                "first",
                "batch=1&input=%7B%220%22%3A%7B%22a%22%3A%7B%7D%7D%7D",
            ),
            Limits {
                max_json_depth: 2,
                ..Limits::default()
            },
        )
        .unwrap_err();
        assert_eq!(error.code_name, "BAD_REQUEST");
    }

    #[test]
    fn enforces_content_length_and_body_limits() {
        let request = Request {
            content_length: Some(9),
            ..post("x", "", b"{}")
        };
        assert_eq!(
            parse_request(
                request,
                Limits {
                    max_body_size: 4,
                    ..Limits::default()
                }
            )
            .unwrap_err()
            .code_name,
            "PAYLOAD_TOO_LARGE"
        );
    }

    #[test]
    fn enforces_get_input_size_limit() {
        let error = parse_request(
            get("x", "input=%7B%7D"),
            Limits {
                max_body_size: 3,
                ..Limits::default()
            },
        )
        .unwrap_err();
        assert_eq!(error.code_name, "PAYLOAD_TOO_LARGE");
    }

    #[test]
    fn enforces_batch_limit() {
        let error = parse_request(
            get("a,b", "batch=1&input=%7B%220%22%3A1%2C%221%22%3A2%7D"),
            Limits {
                max_batch_size: 1,
                ..Limits::default()
            },
        )
        .unwrap_err();
        assert_eq!(error.code_name, "PAYLOAD_TOO_LARGE");
    }

    #[test]
    fn serde_json_recursion_guard_maps_to_bad_request() {
        let nested = format!("{}0{}", "[".repeat(150), "]".repeat(150));
        let error = parse_request(
            post("x", "", nested.as_bytes()),
            Limits {
                max_json_depth: usize::MAX,
                ..Limits::default()
            },
        )
        .unwrap_err();
        assert_eq!(error.code_name, "BAD_REQUEST");
        assert_eq!(error.message, "JSON nesting depth exceeded");
    }

    #[test]
    fn enforces_json_depth_limit() {
        let error = parse_request(
            post("x", "", br#"{"a":{"b":1}}"#),
            Limits {
                max_json_depth: 1,
                ..Limits::default()
            },
        )
        .unwrap_err();
        assert_eq!(error.code_name, "BAD_REQUEST");
    }

    #[test]
    fn query_decoding_handles_plus_and_invalid_utf8() {
        let plus = parse_request(get("x", "input=%22a+b%22"), Limits::default()).unwrap();
        assert_eq!(plus.calls[0].input, "a b");
        let escaped_plus = parse_request(get("x", "input=%22a%2Bb%22"), Limits::default()).unwrap();
        assert_eq!(escaped_plus.calls[0].input, "a+b");

        let invalid_utf8 = parse_request(get("x", "input=%FF%FE"), Limits::default()).unwrap_err();
        assert_eq!(invalid_utf8.code_name, "PARSE_ERROR");
    }

    #[test]
    fn truncated_percent_escape_is_bad_request() {
        assert_eq!(
            parse_request(get("x", "input=%7"), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
    }

    #[test]
    fn missing_and_empty_single_inputs_are_null() {
        let get_request = parse_request(get("x", ""), Limits::default()).unwrap();
        assert_eq!(get_request.calls[0].input, json::Value::Null);
        let post_request = parse_request(post("x", "", b""), Limits::default()).unwrap();
        assert_eq!(post_request.calls[0].input, json::Value::Null);
    }

    #[test]
    fn empty_batched_post_body_is_bad_request() {
        assert_eq!(
            parse_request(post("a,b", "batch=1", b""), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
    }

    #[test]
    fn missing_batch_input_indexes_are_null() {
        let parsed = parse_request(
            post("a,b", "batch=1", br#"{"1":{"value":2}}"#),
            Limits::default(),
        )
        .unwrap();
        assert_eq!(parsed.calls[0].input, json::Value::Null);
        assert_eq!(parsed.calls[1].input["value"], 2);
    }

    #[test]
    fn empty_batch_input_object_means_no_inputs() {
        let parsed = parse_request(post("a,b", "batch=1", b"{}"), Limits::default()).unwrap();
        assert!(parsed.calls.iter().all(|call| call.input.is_null()));
    }

    #[test]
    fn batch_input_indexes_outside_call_count_are_bad_request() {
        assert_eq!(
            parse_request(
                post("a,b", "batch=1", br#"{"0":1,"2":2}"#),
                Limits::default()
            )
            .unwrap_err()
            .code_name,
            "BAD_REQUEST"
        );
    }

    #[test]
    fn actual_body_length_overrides_small_content_length() {
        let request = Request {
            content_length: Some(1),
            ..post("x", "", br#"{"long":"payload"}"#)
        };
        assert_eq!(
            parse_request(
                request,
                Limits {
                    max_body_size: 4,
                    ..Limits::default()
                }
            )
            .unwrap_err()
            .code_name,
            "PAYLOAD_TOO_LARGE"
        );
    }

    #[test]
    fn batched_get_requires_object_input() {
        assert_eq!(
            parse_request(get("a,b", "batch=1&input=%5B1%2C2%5D"), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
    }

    #[test]
    fn query_post_method_override_is_supported() {
        let mut router = Router::new();
        router.register("query", 1, ProcedureKind::Query);
        assert_eq!(router.route("query", HttpMethod::Post).unwrap().id, 1);
    }

    #[test]
    fn malformed_inputs_are_errors() {
        assert_eq!(
            parse_request(get("x", "input=%ZZ"), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
        assert_eq!(
            parse_request(post("x", "", b"{"), Limits::default())
                .unwrap_err()
                .code_name,
            "PARSE_ERROR"
        );
        assert_eq!(
            parse_request(get("x,y", ""), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
    }

    #[test]
    fn batch_input_shape_is_validated() {
        let error =
            parse_request(get("a,b", "batch=1&input=%5B1%2C2%5D"), Limits::default()).unwrap_err();
        assert_eq!(error.code_name, "BAD_REQUEST");
    }

    #[test]
    fn router_merge_and_nest_procedures() {
        let mut parent = Router::new();
        parent.register("users.get", 1, ProcedureKind::Query);
        let mut other = Router::new();
        other.register("users.get", 2, ProcedureKind::Mutation);
        other.register("list", 3, ProcedureKind::Query);
        parent.merge(other);
        assert_eq!(parent.lookup("users.get").unwrap().id, 2);

        let mut child = Router::new();
        child.register("by_id", 4, ProcedureKind::Query);
        parent.nest("posts", &child);
        assert_eq!(parent.lookup("posts.by_id").unwrap().id, 4);
        assert_eq!(parent.route("posts.by_id", HttpMethod::Get).unwrap().id, 4);
    }

    #[test]
    fn router_lookup_and_method_check() {
        let mut router = Router::new();
        router.register("post.list", 7, ProcedureKind::Query);
        router.register("post.create", 8, ProcedureKind::Mutation);
        assert_eq!(router.lookup("post.list").unwrap().id, 7);
        assert_eq!(router.route("post.list", HttpMethod::Post).unwrap().id, 7);
        assert_eq!(
            router
                .route("post.create", HttpMethod::Get)
                .unwrap_err()
                .code_name,
            "METHOD_NOT_SUPPORTED"
        );
    }

    #[test]
    fn single_result_and_error_envelopes_and_status() {
        let success = build_response(vec![Ok(json::json!({"id": 1}))], false);
        assert_eq!(success.http_status, 200);
        let value: json::Value = json::parse(success.body.as_bytes()).unwrap();
        assert_eq!(value["result"]["data"]["id"], 1);
        let failure = build_response(
            vec![Err(TrpcError::new(ErrorCode::NotFound, "missing"))],
            false,
        );
        assert_eq!(failure.http_status, 404);
        let value: json::Value = json::parse(failure.body.as_bytes()).unwrap();
        assert_eq!(value["error"]["data"]["code"], "NOT_FOUND");
    }

    #[test]
    fn batch_envelope_mixes_result_and_error() {
        let response = build_response(
            vec![
                Ok(json::json!(1)),
                Err(TrpcError::new(ErrorCode::NotFound, "missing")),
            ],
            true,
        );
        assert_eq!(response.http_status, 404);
        let value: json::Value = json::parse(response.body.as_bytes()).unwrap();
        assert_eq!(value[0]["result"]["data"], 1);
        assert_eq!(value[1]["error"]["code"], -32004);
    }

    #[test]
    fn rejects_empty_path_and_empty_batch_segment() {
        assert_eq!(
            parse_request(get("", ""), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
        assert_eq!(
            parse_request(get("a,,b", "batch=1&input=%7B%7D"), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
    }

    #[test]
    fn rejects_get_request_with_body() {
        let request = Request {
            method: HttpMethod::Get,
            path: "x",
            query: "",
            body: b"{}",
            content_length: Some(2),
        };
        assert_eq!(
            parse_request(request, Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
    }

    #[test]
    fn rejects_non_numeric_batch_input_keys() {
        assert_eq!(
            parse_request(post("a,b", "batch=1", br#"{"x":1}"#), Limits::default())
                .unwrap_err()
                .code_name,
            "BAD_REQUEST"
        );
    }

    #[test]
    fn lowercase_percent_escapes_decode() {
        let parsed = parse_request(get("x", "input=%7b%22x%22%3a1%7d"), Limits::default()).unwrap();
        assert_eq!(parsed.calls[0].input["x"], 1);
    }

    #[test]
    fn deeply_nested_array_input_is_bad_request() {
        // The object branch of the depth check is covered elsewhere; this is
        // the array branch.
        let error = parse_request(
            post("x", "", b"[[[1]]]"),
            Limits {
                max_json_depth: 1,
                ..Limits::default()
            },
        )
        .unwrap_err();
        assert_eq!(error.code_name, "BAD_REQUEST");
        assert_eq!(error.message, "JSON nesting depth exceeded");
    }

    #[test]
    fn route_reports_not_found_for_unknown_path() {
        let router = Router::new();
        let error = router.route("missing", HttpMethod::Get).unwrap_err();
        assert_eq!(error.code_name, "NOT_FOUND");
        assert!(error.message.contains("\"missing\""));
    }

    #[test]
    fn batch_status_prefers_server_error() {
        let response = build_response(
            vec![
                Err(TrpcError::new(ErrorCode::NotFound, "missing")),
                Ok(json::json!(1)),
                Err(TrpcError::new(ErrorCode::InternalServerError, "boom")),
            ],
            true,
        );
        assert_eq!(response.http_status, 500);
        let value: json::Value = json::parse(response.body.as_bytes()).unwrap();
        assert_eq!(value[0]["error"]["code"], -32004);
        assert_eq!(value[2]["error"]["code"], -32603);
    }

    #[test]
    fn empty_single_result_batch_is_bad_request() {
        let response = build_response(vec![], false);
        assert_eq!(response.http_status, 400);
        let value: json::Value = json::parse(response.body.as_bytes()).unwrap();
        assert_eq!(value["error"]["data"]["code"], "BAD_REQUEST");
    }

    fn arbitrary_json() -> impl proptest::strategy::Strategy<Value = json::Value> {
        use proptest::prelude::{Just, Strategy, any, prop_oneof};
        let leaf = prop_oneof![
            any::<bool>().prop_map(json::Value::Bool),
            any::<i64>().prop_map(|value| json::json!(value)),
            ".{0,24}".prop_map(json::Value::String),
            Just(json::Value::Null),
        ];
        leaf.prop_recursive(4, 64, 8, |inner| {
            prop_oneof![
                proptest::collection::vec(inner.clone(), 0..5).prop_map(json::Value::Array),
                proptest::collection::hash_map("[a-z]{1,8}", inner, 0..5)
                    .prop_map(|items| json::Value::Object(items.into_iter().collect())),
            ]
        })
    }

    proptest::proptest! {
        #[test]
        fn result_envelope_json_round_trip(output in arbitrary_json()) {
            let envelope = json::result_envelope(&output);
            let encoded = json::serialize(&envelope).unwrap();
            let decoded = json::parse(encoded.as_bytes()).unwrap();
            proptest::prop_assert_eq!(&decoded["result"]["data"], &output);
        }

        #[test]
        fn post_input_parse_serialize_round_trip(input in arbitrary_json()) {
            let encoded = json::serialize(&input).unwrap();
            let parsed = parse_request(post("echo", "", encoded.as_bytes()), Limits::default()).unwrap();
            proptest::prop_assert_eq!(&parsed.calls[0].input, &input);
        }
    }
}
