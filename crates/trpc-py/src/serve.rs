// Standalone server: a `hyper` HTTP/1.x server driving a Python ASGI app.
//
// ADR 0013: `hyper` owns socket I/O and HTTP parsing; each request is
// converted into a standard ASGI scope and the existing `mount()` ASGI
// application object answers it. The app coroutine runs on a dedicated
// `asyncio` loop living on a Python daemon thread (created by
// `trpc_rs_py.serve.serve`); it is submitted with
// `asyncio.run_coroutine_threadsafe` and its completion is reported back
// through a done-callback, so a tokio worker is never parked holding the
// GIL.
//
// Backpressure is pull-based: `send()` pushes body chunks through a bounded
// `tokio::sync::mpsc` channel backing the hyper body stream. When the
// channel is full the Python `send` awaits an `asyncio.Event` that Rust
// re-arms via `loop.call_soon_threadsafe` after each drained chunk, so the
// `asyncio` loop never blocks on socket I/O.

use std::{
    convert::Infallible,
    net::SocketAddr,
    pin::Pin,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    task::{Context, Poll},
    time::Duration,
};

use futures_core::Stream;
use http_body_util::{BodyExt, Full, Limited, StreamBody, combinators::BoxBody};
use hyper::{
    Request, Response,
    body::{Bytes, Frame, Incoming},
    header,
    server::conn::http1,
    service::service_fn,
};
use hyper_util::rt::TokioIo;
use pyo3::{
    exceptions::{PyConnectionError, PyOSError, PyRuntimeError},
    intern,
    prelude::*,
    types::{PyBytes, PyDict, PyList},
};
use tokio::{
    net::TcpListener,
    signal,
    sync::{mpsc, oneshot, watch},
    task::JoinSet,
    time,
};
use trpc_core::{
    error::{ErrorCode, TrpcError},
    protocol,
};

/// Buffered response chunks before the socket applies backpressure.
const BODY_CHANNEL_CAPACITY: usize = 16;

/// Interval between `asyncio` loop liveness checks.
const LOOP_WATCH_INTERVAL: Duration = Duration::from_millis(50);

/// Grace period for in-flight connections after a shutdown signal.
const SHUTDOWN_DRAIN: Duration = Duration::from_secs(5);

/// Response head captured from `http.response.start`.
struct ResponseHead {
    /// HTTP status code the ASGI app asked for.
    status: u16,
    /// `(name, value)` byte pairs from the ASGI message.
    headers: Vec<(Vec<u8>, Vec<u8>)>,
}

/// Shared wake-up state between the `AsgiBridge`, the body stream, and the
/// disconnect path.
///
/// `waiting` is set when the Python `send` observed a full channel and is
/// parked in `drained.wait()`; `signal_drained` only crosses into Python when
/// a waiter is known, keeping uncontended chunks GIL-free.
struct Wake {
    /// The `asyncio` loop `send`/`receive` wait on; `call_soon_threadsafe` is
    /// invoked on it.
    loop_: Py<PyAny>,
    /// `(disconnected.set, drained.set)` bound methods, armed by Python once
    /// per request before the app coroutine is scheduled.
    callbacks: Mutex<Option<(Py<PyAny>, Py<PyAny>)>>,
    /// `true` while a Python `send` is parked waiting for channel capacity.
    waiting: AtomicBool,
}

impl Wake {
    /// Borrow the `index`-th callback without a temporary array.
    fn index_pair(pair: &(Py<PyAny>, Py<PyAny>), index: usize) -> &Py<PyAny> {
        if index == 0 { &pair.0 } else { &pair.1 }
    }

    /// Call `loop.call_soon_threadsafe(callback)` for callback `index`
    /// (`0` = disconnected, `1` = drained). `try_attach` keeps interpreter
    /// teardown safe.
    fn fire(&self, index: usize) {
        let _ = Python::try_attach(|py| {
            let callback = self
                .callbacks
                .lock()
                .ok()
                .and_then(|callbacks| {
                    callbacks
                        .as_ref()
                        .map(|pair| Self::index_pair(pair, index).clone_ref(py))
                });
            if let Some(callback) = callback {
                let _ = self.loop_.call_method1(
                    py,
                    intern!(py, "call_soon_threadsafe"),
                    (callback.bind(py),),
                );
            }
        });
    }

    /// Wake every wait point: used on disconnect/close so `receive()` returns
    /// `http.disconnect` and a parked `send()` wakes to observe the closed
    /// channel.
    fn fire_all(&self) {
        let _ = Python::try_attach(|py| {
            let pair = self.callbacks.lock().ok().and_then(|callbacks| {
                callbacks
                    .as_ref()
                    .map(|(a, b)| (a.clone_ref(py), b.clone_ref(py)))
            });
            if let Some((disconnected, drained)) = pair {
                let _ = self.loop_.call_method1(
                    py,
                    intern!(py, "call_soon_threadsafe"),
                    (disconnected.bind(py),),
                );
                let _ = self.loop_.call_method1(
                    py,
                    intern!(py, "call_soon_threadsafe"),
                    (drained.bind(py),),
                );
            }
        });
    }

    /// Re-arm the drain waiter after a chunk left the channel.
    fn signal_drained(&self) {
        if self.waiting.swap(false, Ordering::AcqRel) {
            self.fire(1);
        }
    }
}

/// Per-request bridge object handed to `trpc_rs_py.serve._schedule`.
///
/// Python's `send` calls `start`, `offer`, and `finish`; the done-callback
/// calls `done`. All senders are dropped deterministically on `done` so the
/// response stream ends even if the app forgot its final body message.
#[pyclass(module = "trpc_rs_py._core", name = "_AsgiBridge")]
struct AsgiBridge {
    /// Resolved once by `start` with the `http.response.start` payload.
    head: Mutex<Option<oneshot::Sender<ResponseHead>>>,
    /// Response body chunk channel feeding the hyper body stream.
    body: Mutex<Option<mpsc::Sender<Bytes>>>,
    /// Resolved once by `done` with the app coroutine's failure text.
    done: Mutex<Option<oneshot::Sender<Option<String>>>>,
    /// Shared wake state for disconnect and backpressure signalling.
    wake: Arc<Wake>,
}

fn poisoned() -> PyErr {
    PyRuntimeError::new_err("ASGI bridge lock poisoned")
}

#[pymethods]
impl AsgiBridge {
    /// Store the `asyncio.Event.set` callbacks used for wake-ups.
    ///
    /// `disconnected` wakes `receive()` (it then returns `http.disconnect`);
    /// `drained` wakes a `send` parked on a full channel.
    fn arm(&self, disconnected: Py<PyAny>, drained: Py<PyAny>) -> PyResult<()> {
        *self.wake.callbacks.lock().map_err(|_| poisoned())? = Some((disconnected, drained));
        Ok(())
    }

    /// Capture the `http.response.start` message; first call wins.
    fn start(&self, status: u16, headers: Vec<(Vec<u8>, Vec<u8>)>) -> PyResult<()> {
        if let Some(sender) = self.head.lock().map_err(|_| poisoned())?.take() {
            let _ = sender.send(ResponseHead { status, headers });
        }
        Ok(())
    }

    /// Push one body chunk into the response channel.
    ///
    /// Returns `false` when the channel is full — the caller then awaits the
    /// drained event. Raises `ConnectionError` once the client side is gone,
    /// which the adapter treats as disconnect.
    fn offer(&self, chunk: &[u8]) -> PyResult<bool> {
        let guard = self.body.lock().map_err(|_| poisoned())?;
        let Some(sender) = guard.as_ref() else {
            return Err(PyConnectionError::new_err("response channel closed"));
        };
        if sender.is_closed() {
            return Err(PyConnectionError::new_err("client disconnected"));
        }
        match sender.try_send(Bytes::copy_from_slice(chunk)) {
            Ok(()) => Ok(true),
            Err(mpsc::error::TrySendError::Full(_)) => {
                self.wake.waiting.store(true, Ordering::Release);
                Ok(false)
            }
            Err(mpsc::error::TrySendError::Closed(_)) => {
                Err(PyConnectionError::new_err("client disconnected"))
            }
        }
    }

    /// End the response body (the app's final `http.response.body`).
    fn finish(&self) -> PyResult<()> {
        self.body.lock().map_err(|_| poisoned())?.take();
        Ok(())
    }

    /// Report app coroutine completion; also ends the body channel so the
    /// response stream terminates even when `finish` was never called.
    fn done(&self, error: Option<Bound<'_, PyAny>>) -> PyResult<()> {
        let text = error.map(|error| {
            error.str().map_or_else(
                |_| "error".to_owned(),
                |string| string.to_string_lossy().into_owned(),
            )
        });
        self.body.lock().map_err(|_| poisoned())?.take();
        if let Some(sender) = self.done.lock().map_err(|_| poisoned())?.take() {
            let _ = sender.send(text);
        }
        Ok(())
    }
}

/// Response body stream for hyper: yields each channel chunk and re-arms the
/// Python drain waiter after capacity frees up.
///
/// `Drop` fires disconnect + drained wake-ups: when hyper drops the body —
/// client disconnect, mid-request abort, or response completion — a `send`
/// parked in `drained.wait()` wakes to find the channel closed, and a
/// `receive()` parked in `disconnected.wait()` returns `http.disconnect`,
/// cancelling in-flight subscription generators.
struct ResponseChannel {
    /// Channel receiving body chunks from the Python `send` callable.
    rx: mpsc::Receiver<Bytes>,
    /// Wake state shared with the request's `AsgiBridge`.
    wake: Arc<Wake>,
}

impl Stream for ResponseChannel {
    type Item = Result<Frame<Bytes>, Infallible>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        match self.rx.poll_recv(cx) {
            Poll::Ready(Some(bytes)) => {
                self.wake.signal_drained();
                Poll::Ready(Some(Ok(Frame::data(bytes))))
            }
            Poll::Ready(None) => Poll::Ready(None),
            Poll::Pending => Poll::Pending,
        }
    }
}

impl Drop for ResponseChannel {
    fn drop(&mut self) {
        self.wake.fire_all();
    }
}

/// Server state shared by every connection.
struct ServerCtx {
    /// The ASGI application callable.
    app: Py<PyAny>,
    /// The dedicated `asyncio` event loop object.
    loop_: Py<PyAny>,
    /// `trpc_rs_py.serve._schedule` — per-request bridge factory.
    schedule: Py<PyAny>,
    /// Bind host reported in the ASGI scope `server` tuple.
    host: String,
    /// Bind port reported in the ASGI scope `server` tuple.
    port: u16,
    /// Transport-level request body cap, mirroring the app's own limit.
    max_body_size: usize,
}

/// Per-connection state: the shared server context plus the peer address.
struct ConnCtx {
    /// Shared server state.
    server: Arc<ServerCtx>,
    /// Remote peer for the ASGI scope `client` tuple.
    peer: SocketAddr,
}

fn http_version_name(version: http::Version) -> &'static str {
    match version {
        http::Version::HTTP_09 => "0.9",
        http::Version::HTTP_10 => "1.0",
        http::Version::HTTP_2 => "2",
        http::Version::HTTP_3 => "3",
        _ => "1.1",
    }
}

fn hex(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

/// Percent-decode a URL path into the unquoted `str` ASGI expects; malformed
/// escapes pass through literally and invalid UTF-8 is replaced.
fn decode_path(raw: &str) -> String {
    if !raw.contains('%') {
        return raw.to_owned();
    }
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%'
            && index + 2 < bytes.len()
            && let (Some(high), Some(low)) = (hex(bytes[index + 1]), hex(bytes[index + 2]))
        {
            out.push((high << 4) | low);
            index += 3;
            continue;
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Build the ASGI `http` scope dict exactly as the test harness and the
/// WSGI bridge do (same keys, same shapes).
fn build_scope<'py>(
    py: Python<'py>,
    parts: &http::request::Parts,
    conn_ctx: &ConnCtx,
) -> PyResult<Bound<'py, PyAny>> {
    let ctx = &conn_ctx.server;
    let scope = PyDict::new(py);
    scope.set_item(intern!(py, "type"), intern!(py, "http"))?;
    let asgi = PyDict::new(py);
    asgi.set_item(intern!(py, "version"), "3.0")?;
    asgi.set_item(intern!(py, "spec_version"), "2.3")?;
    scope.set_item(intern!(py, "asgi"), asgi)?;
    scope.set_item(
        intern!(py, "http_version"),
        http_version_name(parts.version),
    )?;
    scope.set_item(intern!(py, "method"), parts.method.as_str())?;
    scope.set_item(intern!(py, "scheme"), "http")?;
    let raw_path = parts.uri.path();
    scope.set_item(intern!(py, "path"), decode_path(raw_path))?;
    scope.set_item(
        intern!(py, "raw_path"),
        PyBytes::new(py, raw_path.as_bytes()),
    )?;
    scope.set_item(
        intern!(py, "query_string"),
        PyBytes::new(py, parts.uri.query().unwrap_or_default().as_bytes()),
    )?;
    scope.set_item(intern!(py, "root_path"), "")?;
    let headers = PyList::empty(py);
    for (name, value) in &parts.headers {
        headers.append((
            PyBytes::new(py, name.as_str().as_bytes()),
            PyBytes::new(py, value.as_bytes()),
        ))?;
    }
    scope.set_item(intern!(py, "headers"), headers)?;
    scope.set_item(
        intern!(py, "client"),
        (conn_ctx.peer.ip().to_string(), conn_ctx.peer.port()),
    )?;
    scope.set_item(intern!(py, "server"), (ctx.host.as_str(), ctx.port))?;
    Ok(scope.into_any())
}

/// GIL section of one request: synthesize the scope, create the bridge, and
/// hand `app(scope, receive, send)` to `trpc_rs_py.serve._schedule`, which
/// schedules it on the `asyncio` loop.
///
/// Returns the receivers Rust awaits plus the response-body channel (which
/// becomes the hyper body stream once `http.response.start` arrives).
fn dispatch(
    py: Python<'_>,
    parts: &http::request::Parts,
    body: &Bytes,
    conn_ctx: &ConnCtx,
) -> PyResult<(
    oneshot::Receiver<ResponseHead>,
    ResponseChannel,
    oneshot::Receiver<Option<String>>,
)> {
    let ctx = &conn_ctx.server;
    let wake = Arc::new(Wake {
        loop_: ctx.loop_.clone_ref(py),
        callbacks: Mutex::new(None),
        waiting: AtomicBool::new(false),
    });
    let (head_tx, head_rx) = oneshot::channel();
    let (body_tx, body_rx) = mpsc::channel(BODY_CHANNEL_CAPACITY);
    let (done_tx, done_rx) = oneshot::channel();
    let bridge = Py::new(
        py,
        AsgiBridge {
            head: Mutex::new(Some(head_tx)),
            body: Mutex::new(Some(body_tx)),
            done: Mutex::new(Some(done_tx)),
            wake: Arc::clone(&wake),
        },
    )?;
    let scope = build_scope(py, parts, conn_ctx)?;
    ctx.schedule.bind(py).call1((
        ctx.app.bind(py),
        scope,
        PyBytes::new(py, body.as_ref()),
        bridge.bind(py),
        ctx.loop_.bind(py),
    ))?;
    Ok((
        head_rx,
        ResponseChannel { rx: body_rx, wake },
        done_rx,
    ))
}

fn too_large_response() -> Response<BoxBody<Bytes, Infallible>> {
    // Same envelope mount() emits for an over-limit request.
    let response = protocol::build_response(
        vec![Err(TrpcError::new(
            ErrorCode::PayloadTooLarge,
            "Request body exceeds the configured limit",
        ))],
        false,
    );
    let mut out = Response::new(Full::new(Bytes::from(response.body)).boxed());
    if let Ok(status) = http::StatusCode::from_u16(response.http_status) {
        *out.status_mut() = status;
    }
    out.headers_mut().insert(
        header::CONTENT_TYPE,
        header::HeaderValue::from_static("application/json"),
    );
    out
}

fn internal_error_response() -> Response<BoxBody<Bytes, Infallible>> {
    let mut out = Response::new(Full::new(Bytes::from_static(b"Internal Server Error")).boxed());
    *out.status_mut() = http::StatusCode::INTERNAL_SERVER_ERROR;
    out.headers_mut().insert(
        header::CONTENT_TYPE,
        header::HeaderValue::from_static("text/plain"),
    );
    out
}

fn asgi_response(
    head: ResponseHead,
    body: BoxBody<Bytes, Infallible>,
) -> Response<BoxBody<Bytes, Infallible>> {
    let mut response = Response::new(body);
    if let Ok(status) = http::StatusCode::from_u16(head.status) {
        *response.status_mut() = status;
    }
    let headers = response.headers_mut();
    for (name, value) in head.headers {
        if let (Ok(name), Ok(value)) = (
            header::HeaderName::from_bytes(&name),
            header::HeaderValue::from_bytes(&value),
        ) {
            headers.append(name, value);
        }
    }
    response
}

/// Serve one HTTP request: cap the body, hand off to the ASGI app, and
/// stream the response it produces.
async fn handle_request(
    request: Request<Incoming>,
    conn_ctx: Arc<ConnCtx>,
) -> Result<Response<BoxBody<Bytes, Infallible>>, Infallible> {
    let (parts, incoming) = request.into_parts();
    let declared_too_large = parts
        .headers
        .get(header::CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|text| text.trim().parse::<usize>().ok())
        .is_some_and(|length| length > conn_ctx.server.max_body_size);
    if declared_too_large {
        return Ok(too_large_response());
    }
    let body = match Limited::new(incoming, conn_ctx.server.max_body_size)
        .collect()
        .await
    {
        Ok(collected) => collected.to_bytes(),
        Err(_length_limit) => return Ok(too_large_response()),
    };

    // Never hold the GIL across an await: this attach returns plain Rust
    // receivers before the next suspension point.
    let dispatched = Python::attach(|py| dispatch(py, &parts, &body, &conn_ctx));
    let (mut head_rx, stream, mut done_rx) = match dispatched {
        Ok(channels) => channels,
        Err(_error) => return Ok(internal_error_response()),
    };

    tokio::select! {
        // `biased` prefers an already-sent head over an app-done race, so a
        // response that was produced wins over the completion signal.
        biased;
        head = &mut head_rx => match head {
            Ok(head) => Ok(asgi_response(
                head,
                StreamBody::new(stream).boxed(),
            )),
            Err(_closed) => Ok(internal_error_response()),
        },
        done = &mut done_rx => Ok(match done {
            Ok(None | Some(_)) | Err(_) => internal_error_response(),
        }),
    }
    // `stream` is dropped on every non-head path, firing disconnect wake-ups
    // so the app coroutine cleans up instead of leaking on the loop.
}

/// Serve one accepted connection until it closes or shutdown begins, then
/// drain in-flight requests gracefully.
async fn serve_conn(
    stream: tokio::net::TcpStream,
    peer: SocketAddr,
    server: Arc<ServerCtx>,
    mut shutdown: watch::Receiver<()>,
) {
    let _ = stream.set_nodelay(true);
    let conn_ctx = Arc::new(ConnCtx { server, peer });
    let service = service_fn(move |request| handle_request(request, conn_ctx.clone()));
    let connection = http1::Builder::new().serve_connection(TokioIo::new(stream), service);
    let mut connection = std::pin::pin!(connection);
    tokio::select! {
        result = connection.as_mut() => {
            let _ = result;
        }
        changed = shutdown.changed() => {
            if changed.is_ok() {
                connection.as_mut().graceful_shutdown();
            }
            let _ = connection.await;
        }
    }
}

/// Watch the host `asyncio` loop: when it stops (the Python side is gone),
/// trigger the same graceful shutdown as Ctrl-C. This is also the test hook —
/// stopping the loop returns `serve` deterministically without signals.
async fn loop_watch(loop_: Py<PyAny>, shutdown: watch::Sender<()>) {
    loop {
        time::sleep(LOOP_WATCH_INTERVAL).await;
        let running = Python::try_attach(|py| {
            loop_
                .bind(py)
                .call_method0(intern!(py, "is_running"))
                .and_then(|value| value.extract::<bool>())
                .unwrap_or(false)
        })
        .unwrap_or(false);
        if !running {
            let _ = shutdown.send(());
            return;
        }
    }
}

async fn serve_async(ctx: Arc<ServerCtx>) -> PyResult<()> {
    let listener = TcpListener::bind((ctx.host.as_str(), ctx.port))
        .await
        .map_err(|error| {
            PyOSError::new_err(format!(
                "failed to bind {}:{}: {error}",
                ctx.host, ctx.port
            ))
        })?;

    let (shutdown_tx, _) = watch::channel(());
    tokio::spawn({
        let shutdown_tx = shutdown_tx.clone();
        async move {
            let _ = signal::ctrl_c().await;
            let _ = shutdown_tx.send(());
        }
    });
    tokio::spawn(loop_watch(
        Python::attach(|py| ctx.loop_.clone_ref(py)),
        shutdown_tx.clone(),
    ));
    let mut shutdown_rx = shutdown_tx.subscribe();
    let mut connections = JoinSet::new();
    loop {
        tokio::select! {
            _ = shutdown_rx.changed() => break,
            accepted = listener.accept() => {
                if let Ok((stream, peer)) = accepted {
                    connections.spawn(serve_conn(
                        stream,
                        peer,
                        Arc::clone(&ctx),
                        shutdown_tx.subscribe(),
                    ));
                }
            }
        }
    }
    // Connections already received the shutdown broadcast and are draining
    // via graceful_shutdown; give them a bounded window to finish.
    let _ = time::timeout(SHUTDOWN_DRAIN, async {
        while connections.join_next().await.is_some() {}
    })
    .await;
    Ok(())
}

fn run_server(ctx: Arc<ServerCtx>) -> PyResult<()> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|error| PyRuntimeError::new_err(format!("failed to start runtime: {error}")))?;
    runtime.block_on(serve_async(ctx))
}

/// Serve an ASGI app on the embedded `hyper` server until shutdown.
///
/// Blocks the calling thread (with the GIL released) until Ctrl-C is
/// received or `loop_` stops running. `loop_` must be an `asyncio` loop
/// executing on its own thread — `trpc_rs_py.serve.serve` owns that
/// lifecycle; `_core.serve` also returns when the loop dies.
///
/// `max_body_size` caps request bodies at the transport level, mirroring the
/// app's own configured limit; it defaults to the protocol limit.
///
/// # Errors
///
/// Raises `OSError` when the socket cannot be bound, and `RuntimeError`
/// when the `tokio` runtime fails to start.
#[pyfunction]
#[pyo3(signature = (app, loop_, host, port, *, max_body_size=None))]
pub(crate) fn serve(
    app: Py<PyAny>,
    loop_: Py<PyAny>,
    host: String,
    port: u16,
    max_body_size: Option<usize>,
    py: Python<'_>,
) -> PyResult<()> {
    let schedule = py
        .import(intern!(py, "trpc_rs_py.serve"))?
        .getattr(intern!(py, "_schedule"))?
        .unbind();
    let ctx = Arc::new(ServerCtx {
        app,
        loop_,
        schedule,
        host,
        port,
        max_body_size: max_body_size.unwrap_or(protocol::DEFAULT_MAX_BODY_SIZE),
    });
    py.detach(move || run_server(ctx))
}
