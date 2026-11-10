"""REST + OpenAPI ASGI sub-app for `RestRoute`-annotated procedures.

`mount_rest` returns a pure-ASGI app (no framework imports) that serves, under
whatever prefix it is mounted at:

* one REST endpoint per procedure registered with
  ``rest=RestRoute(method, path)``,
* `GET /openapi.json` — the OpenAPI 3.1 document from `generate_openapi`,
* `GET /docs` — a small vendored docs UI (no CDN dependency), disabled in
  production mode unless explicitly enabled with ``docs=True``.

Requests share the procedure's normal input and output validators, so REST
and tRPC validate identically; only the wire shapes differ. Responses carry
the handler's return value directly (no tRPC envelope); errors use the fixed
shape ``{"error": {"code": "...", "message": "..."}}`` with the HTTP status
mapped from the `TRPCError` code, and the same production redaction policy as
the tRPC adapter.

Example:
    ```python
    from fastapi import FastAPI

    from trpc_rs_py import RestRoute, Router, mount, mount_rest

    router = Router()

    @router.query(rest=RestRoute("GET", "/posts/{post_id}"))
    def by_id(post_id: int) -> str:
        return str(post_id)

    app = FastAPI()
    app.mount("/trpc", mount(router))
    app.mount("/api", mount_rest(router))
    # tRPC at /trpc/*, REST at /api/posts/3, OpenAPI at /api/openapi.json.
    ```
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
from typing import Any, cast
from urllib.parse import parse_qs, unquote

from ._core import CallResult, Limits, build_response
from .asgi import ASGIApp, Receive, Scope, Send
from .context import (
    ContextFactory,
    ctx_injection,
    inject_ctx,
    request_context_from_scope,
)
from .errors import TRPCError, TRPCErrorCode, error_data_payload, http_status_for
from .openapi import generate_openapi
from .router import Procedure, ProcedureKind, RestRoute, Router
from .validation import (
    InputValidator,
    OutputValidator,
    build_input_validator,
    build_output_validator,
)

__all__ = ["mount_rest"]

_PRODUCTION_INTERNAL_MESSAGE = "Internal server error"

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

_DOCS_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>API docs</title>
<style>
body{font-family:ui-monospace,monospace;margin:2rem;max-width:60rem;color:#111}
.op{border:1px solid #ddd;border-radius:6px;padding:.75rem 1rem;margin:.5rem 0}
.m{font-weight:700;display:inline-block;min-width:4.5rem}
.GET{color:#0a7d2c}.POST{color:#0b5bd3}.PUT{color:#a35b00}
.PATCH{color:#7a4bbf}.DELETE{color:#b3261e}
textarea,input{width:100%;box-sizing:border-box;font:inherit;margin:.25rem 0}
button{font:inherit;padding:.35rem .9rem;cursor:pointer}
pre{background:#f5f5f5;padding:.75rem;border-radius:6px;overflow:auto}
h3{margin:.6rem 0 .2rem;font-size:.95rem}
</style></head><body>
<h1 id=t>API</h1><div id=ops></div>
<script>
(async()=>{
const spec=await (await fetch("openapi.json")).json();
document.getElementById("t").textContent=spec.info.title+" "+spec.info.version;
const ops=document.getElementById("ops");
for(const [p,item] of Object.entries(spec.paths)){
 for(const [m,op] of Object.entries(item)){
  const d=document.createElement("div");d.className="op";
  const h=document.createElement("div");
  h.innerHTML=`<span class="m ${m.toUpperCase()}">${m.toUpperCase()}</span> <code>${p}</code> <small>${op.operationId||""}</small>`;
  d.appendChild(h);
  const qs=(op.parameters||[]).filter(x=>x.in==="query");
  const ps=(op.parameters||[]).filter(x=>x.in==="path");
  const fields=[];
  for(const x of ps){const i=document.createElement("input");i.placeholder=x.name+" (path)";i.dataset.pathParam=x.name;fields.push(i);d.appendChild(i)}
  for(const x of qs){const i=document.createElement("input");i.placeholder=x.name+" (query)";i.dataset.queryParam=x.name;fields.push(i);d.appendChild(i)}
  let ta=null;
  if(op.requestBody){ta=document.createElement("textarea");ta.rows=4;ta.placeholder='request body (JSON), e.g. {"name":"x"}';d.appendChild(ta)}
  const b=document.createElement("button");b.textContent="Send";d.appendChild(b);
  const out=document.createElement("pre");out.style.display="none";d.appendChild(out);
  b.onclick=async()=>{
   let url=p;const q=[];
   for(const i of fields){
    if(!i.value)continue;
    if(i.dataset.pathParam)url=url.replace("{"+i.dataset.pathParam+"}",encodeURIComponent(i.value));
    else q.push(encodeURIComponent(i.dataset.queryParam)+"="+encodeURIComponent(i.value));}
   if(q.length)url+="?"+q.join("&");
   const opt={method:m.toUpperCase(),headers:{}};
   if(ta&&ta.value){opt.headers["content-type"]="application/json";opt.body=ta.value;}
   const r=await fetch(url,opt);
   const t=await r.text();let pretty=t;
   try{pretty=JSON.stringify(JSON.parse(t),null,2)}catch{}
   out.style.display="block";out.textContent=r.status+" "+r.statusText+"\\n"+pretty;};
  ops.appendChild(d);}}
})().catch(e=>{document.body.innerHTML="<pre>Failed to load openapi.json: "+e+"</pre>"});
</script></body></html>
"""  # noqa: E501


@dataclasses.dataclass(frozen=True)
class _Route:
    """One compiled REST endpoint for a `RestRoute` procedure."""

    rest: RestRoute
    pattern: Any
    procedure: Procedure
    input_validator: InputValidator | None
    output_validator: OutputValidator | None


def mount_rest(
    router: Router,
    *,
    title: str = "trpc-rs-py API",
    version: str = "0.0.0",
    description: str | None = None,
    docs: bool | None = None,
    production: bool = False,
    limits: Limits | None = None,
    context_factory: ContextFactory | None = None,
) -> ASGIApp:
    """Serve `RestRoute` procedures as REST endpoints plus OpenAPI + docs.

    Builds the route table and the OpenAPI document once at mount time.
    Dispatch is pure ASGI: the request path (with `root_path` stripped, so
    framework mounting works) is matched against each route's compiled path
    template; `{name}` segments bind path parameters, and the remaining
    parameters come from the query string (GET/DELETE) or JSON body
    (POST/PUT/PATCH). The merged mapping passes through the procedure's
    normal Pydantic input validator, so REST and tRPC validate identically.

    Args:
        router: Router whose `rest`-annotated procedures are served.
        title: `info.title` in the generated document and docs page.
        version: `info.version` in the generated document.
        description: Optional `info.description`.
        docs: Serve the `/docs` page and `/openapi.json`. `None` (default)
            means enabled except in production mode; pass `True` to keep the
            docs available in production explicitly.
        production: Production mode — redacts internal error details and
            (with `docs=None`) disables the docs UI.
        limits: Request limits; protocol defaults when `None`.
        context_factory: Optional factory called once per request with the
            `RequestContext`; the returned object is injected into handlers
            whose first parameter is named `ctx`. Defaults to the
            `RequestContext` itself.

    Returns:
        An ASGI application to mount under any prefix.

    Raises:
        ValueError: If two procedures declare the same method + path, or a
            subscription procedure somehow carries `RestRoute` metadata.

    Example:
        ```python
        from trpc_rs_py import RestRoute, Router, mount_rest

        router = Router()

        @router.query(rest=RestRoute("GET", "/health"))
        def health() -> bool:
            return True

        app.mount("/api", mount_rest(router))
        ```
    """
    resolved_limits = limits or Limits()
    document = generate_openapi(router, title=title, version=version, description=description)
    document_body = json.dumps(document)
    docs_enabled = (not production) if docs is None else docs
    routes = _build_routes(router)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        """Dispatch one request to the REST route table or docs endpoints."""
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        method: str = scope["method"]
        request_path = _request_path(scope, "")
        if request_path == "/openapi.json":
            if docs_enabled:
                await _send_text(send, 200, document_body, "application/json")
            else:
                await _send_error(send, 404, "NOT_FOUND", "Not found")
            return
        if request_path == "/docs":
            if docs_enabled and method == "GET":
                await _send_text(send, 200, _DOCS_PAGE, "text/html; charset=utf-8")
            else:
                await _send_error(send, 404, "NOT_FOUND", "Not found")
            return
        matched_path = False
        for route in routes:
            match = route.pattern.match(request_path)
            if match is None:
                continue
            matched_path = True
            if route.rest.method != method:
                continue
            await _serve_route(
                route, match, scope, receive, send, resolved_limits, production, context_factory
            )
            return
        if matched_path:
            await _send_error(send, 405, "METHOD_NOT_SUPPORTED", "Method not allowed")
        else:
            await _send_error(send, 404, "NOT_FOUND", "Not found")

    return app


def serve_attached(
    router: Router,
    *,
    prefix: str,
    title: str = "trpc-rs-py API",
    version: str = "0.0.0",
    description: str | None = None,
    docs: bool | None = None,
    production: bool = False,
    limits: Limits | None = None,
    context_factory: ContextFactory | None = None,
) -> ASGIApp:
    """Internal variant of `mount_rest` for `attach(..., rest_path=...)`.

    Identical to `mount_rest` except the given `prefix` is stripped from the
    scope path before routing (middleware attach never receives `root_path`
    adjustments, so the configured prefix must be removed here).

    Args:
        router: Router whose `rest`-annotated procedures are served.
        prefix: Path prefix this app is attached at, e.g. `/api`.
        title: `info.title` in the generated document and docs page.
        version: `info.version` in the generated document.
        description: Optional `info.description`.
        docs: Docs UI toggle; see `mount_rest`.
        production: Production error redaction; see `mount_rest`.
        limits: Request limits; protocol defaults when `None`.
        context_factory: Optional per-request context factory; see
            `mount_rest`.

    Returns:
        An ASGI application dispatching under `prefix`.
    """
    inner = mount_rest(
        router,
        title=title,
        version=version,
        description=description,
        docs=docs,
        production=production,
        limits=limits,
        context_factory=context_factory,
    )

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        """Forward the scope with `prefix` folded into `root_path`."""
        if scope["type"] == "http":
            scope = dict(scope)
            scope["root_path"] = (scope.get("root_path") or "") + prefix
        await inner(scope, receive, send)

    return app


def _build_routes(router: Router) -> list[_Route]:
    """Compile `router`'s `RestRoute` procedures into the dispatch table.

    Raises:
        ValueError: On a duplicate (method, path) pair or a subscription
            carrying REST metadata.
    """
    routes: list[_Route] = []
    seen: set[tuple[str, str]] = set()
    for procedure in router.procedures.values():
        rest = procedure.rest
        if rest is None:
            continue
        if procedure.kind is ProcedureKind.SUBSCRIPTION:
            msg = f"Subscription {procedure.path!r} cannot be served over REST"
            raise ValueError(msg)
        key = (rest.method, rest.path)
        if key in seen:
            msg = f"Duplicate REST route {rest.method} {rest.path}"
            raise ValueError(msg)
        seen.add(key)
        routes.append(
            _Route(
                rest=rest,
                pattern=rest.regex(),
                procedure=procedure,
                input_validator=build_input_validator(procedure.handler),
                output_validator=build_output_validator(procedure.handler),
            )
        )
    return routes


def _request_path(scope: Scope, prefix: str) -> str:
    """Return the scope path relative to the mount/attach prefix.

    Handles both mounting conventions: `root_path` set by the framework
    (Starlette/FastAPI `app.mount`) or the explicit `prefix` used by
    `serve_attached`.
    """
    path: str = scope["path"]
    root = prefix or scope.get("root_path") or ""
    if root and path.startswith(root):
        path = path[len(root) :]
    return path or "/"


async def _serve_route(
    route: _Route,
    match: Any,
    scope: Scope,
    receive: Receive,
    send: Send,
    limits: Limits,
    production: bool,
    context_factory: ContextFactory | None = None,
) -> None:
    """Run one matched REST call and send its JSON response."""
    request_context = request_context_from_scope(scope)
    try:
        ctx = context_factory(request_context) if context_factory is not None else request_context
        if inspect.isawaitable(ctx):
            ctx = await ctx
    except Exception as error:
        message = "Internal server error" if production else repr(error)
        await _send_error(send, 500, "INTERNAL_SERVER_ERROR", message)
        return
    input_value = await _collect_input(route, match, scope, receive, limits)
    if isinstance(input_value, _InputError):
        await _send_error(send, 400, "BAD_REQUEST", "Invalid request body")
        return
    status, body = await _run(route, input_value, production, ctx)
    await _send_text(send, status, json.dumps(body), "application/json")


class _InputError:
    """Marker for a malformed request body during input collection."""


async def _collect_input(
    route: _Route, match: Any, scope: Scope, receive: Receive, limits: Limits
) -> Any:
    """Assemble `{name: value}` input from path params + query string/body."""
    input_value: dict[str, Any] = {
        name: unquote(value) for name, value in match.groupdict().items()
    }
    if route.rest.method in _BODY_METHODS:
        body = await _read_body(receive, limits.max_body_size)
        if body.strip():
            try:
                decoded = json.loads(body)
            except ValueError:
                return _InputError()
            if isinstance(decoded, dict):
                input_value.update(cast(dict[str, Any], decoded))
            else:
                input_value["body"] = decoded
    else:
        query: bytes = scope.get("query_string", b"")
        for name, values in parse_qs(query.decode("utf-8"), keep_blank_values=True).items():
            if name in input_value:
                continue
            input_value[name] = values[0] if len(values) == 1 else values
    if not input_value:
        return None
    return input_value


async def _read_body(receive: Receive, limit: int) -> bytes:
    """Read the request body up to `limit` bytes."""
    parts: list[bytes] = []
    remaining = limit
    while True:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunk: bytes = message.get("body", b"")
        if len(chunk) > remaining:
            return b""
        remaining -= len(chunk)
        parts.append(chunk)
        if not message.get("more_body"):
            return b"".join(parts)


async def _run(
    route: _Route, input_value: Any, production: bool, ctx: Any = None
) -> tuple[int, Any]:
    """Execute the procedure and produce `(status, json_body)`.

    Mirrors `_run_call` in `asgi.py` — same validator pipeline and the same
    production redaction policy — but returns a REST-shaped body instead of a
    tRPC envelope.
    """
    procedure = route.procedure
    try:
        if route.input_validator is not None:
            args, kwargs = route.input_validator(input_value)
        else:
            args, kwargs = (), {}
        args, kwargs = inject_ctx(ctx_injection(procedure.handler), args, kwargs, ctx)
    except Exception:
        return 400, _error_object("BAD_REQUEST", f"Invalid input for procedure {procedure.path}")
    try:
        if inspect.iscoroutinefunction(procedure.handler):
            data = await procedure.handler(*args, **kwargs)
        else:
            data = await asyncio.to_thread(procedure.handler, *args, **kwargs)
    except TRPCError as error:
        message = error.message
        if production and error.code is TRPCErrorCode.INTERNAL_SERVER_ERROR:
            message = _PRODUCTION_INTERNAL_MESSAGE
        body = _error_object(error.code.value, message)
        payload = error_data_payload(error)
        if isinstance(payload, dict):
            body["error"]["data"] = payload
        elif payload is not None:
            body["error"]["data"] = {"data": payload}
        return http_status_for(error.code), body
    except Exception as error:
        message = _PRODUCTION_INTERNAL_MESSAGE if production else repr(error)
        return 500, _error_object("INTERNAL_SERVER_ERROR", message)
    if route.output_validator is not None:
        try:
            data = route.output_validator(data)
        except Exception:
            message = (
                _PRODUCTION_INTERNAL_MESSAGE if production else "Invalid output from procedure"
            )
            return 500, _error_object("INTERNAL_SERVER_ERROR", message)
    try:
        _, envelope = build_response([CallResult.ok(data)], False)
        return 200, json.loads(envelope)["result"]["data"]
    except Exception:
        message = _PRODUCTION_INTERNAL_MESSAGE if production else "Invalid output from procedure"
        return 500, _error_object("INTERNAL_SERVER_ERROR", message)


def _error_object(code: str, message: str) -> dict[str, Any]:
    """Build the fixed REST error body ``{"error": {"code", "message"}}``."""
    return {"error": {"code": code, "message": message}}


async def _send_error(send: Send, status: int, code: str, message: str) -> None:
    """Send a REST error response."""
    await _send_text(send, status, json.dumps(_error_object(code, message)), "application/json")


async def _send_text(send: Send, status: int, body: str, content_type: str) -> None:
    """Send a complete HTTP response with `body` and `content_type`."""
    payload = body.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type.encode("ascii")),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
