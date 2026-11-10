# Playground panel

Every adapter can serve a built-in playground at `{prefix}/panel` — a
single self-contained page (no CDN, no external assets, everything inside
the wheel) that lists the router's procedures, generates input forms from
their JSON schemas, calls procedures over the real tRPC wire protocol, and
shows streaming subscriptions live via `EventSource` (MISSION 2.7).

```python
app.mount("/trpc", mount(router))
# open http://localhost:8000/trpc/panel in the browser
```

The page offers:

* a searchable procedure list grouped by namespace, with `query`,
  `mutation` and `subscription` badges (and the REST route, when the
  procedure carries `RestRoute` metadata);
* a detail pane showing the input and output JSON schemas;
* a form generated from the input schema — text/number/checkbox/select
  fields for scalar and enum parameters, JSON textareas for complex ones,
  and a raw JSON input for schemas that are not plain objects;
* a **Call** button issuing a GET query or POST mutation and
  pretty-printing the result envelope (or error envelope);
* a **Subscribe** button for subscriptions, streaming events into a live
  pane and tracking `lastEventId` so a resume value can be pasted back in.

## Enabling and disabling

The `panel` parameter exists on every entry point and follows the same
rule as the REST docs UI (MISSION section 7):

| `panel=` | Development (`production=False`) | Production |
| --- | --- | --- |
| `None` (default) | **on** | **off** |
| `True` | on | on (explicit opt-in) |
| `False` | off | off |

```python
mount(router)                        # panel on (dev)
mount(router, production=True)       # panel off
mount(router, production=True, panel=True)  # panel on anyway
```

> **Do not ship the panel to production casually.** It exposes the name,
> kind, and input/output schemas of every procedure to anyone who can
> reach the endpoint — an attack-surface map of your API. Enable it in
> production only behind authentication of your own.

## Endpoints and the introspection document

Both endpoints live under the mount's own prefix and are answered **before**
procedure dispatch — context factories and middleware never run for them:

| Endpoint | Response |
| --- | --- |
| `GET {prefix}/panel` | The playground page (`text/html`) |
| `GET {prefix}/panel/schema` | The introspection document (`application/json`) |

Unknown subpaths (`{prefix}/panel/anything`) return `404`; non-GET methods
return `405` with `Allow: GET`. The document is built once at mount time —
procedures registered later do not appear until the adapter is rebuilt.

```json
{"procedures": [
  {"path": "posts.by_id", "kind": "query",
   "input": {"type": "object",
             "properties": {"post_id": {"type": "integer"}},
             "required": ["post_id"]},
   "output": {"type": "string"},
   "rest": {"method": "GET", "path": "/posts/{post_id}"}}
]}
```

`input` is the object schema of the wire input (one property per handler
parameter, `ctx` excluded); `output` is the return annotation's schema —
for subscriptions, the schema of one yielded item. `rest` appears only
when the procedure has `RestRoute` metadata. Annotations Pydantic cannot
turn into JSON Schema yield `null` for that side — introspection never
breaks over an unserializable annotation.

## Every adapter

`panel` is forwarded through the whole adapter family, so the playground
works identically under FastAPI/Starlette, Flask/WSGI, and Django:

```python
mount(router, panel=True)
attach(app, router, panel=True)
mount_wsgi(router, panel=True)
attach_flask(app, router, panel=True)
trpc_view(router, panel=True)            # and trpc_view_async / urlpatterns
```

Under Django the panel sits at `{prefix}/panel` of whichever URL prefix
`trpc_view`/`urlpatterns` is mounted on (e.g. `/trpc/panel`).

## The `panel` name collision

The panel is dispatched *before* procedure lookup, so a procedure literally
named `panel` is **shadowed** while the panel is enabled — `GET
{prefix}/panel` serves the page, not the procedure. Dotted paths are
unaffected (`panel.metrics` still dispatches to the procedure), and
disabling the panel frees the `panel` procedure path again.
