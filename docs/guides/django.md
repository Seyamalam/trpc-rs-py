# Django

Import from `trpc_rs_py.django` — Django stays an optional dependency. Two
views serve the router: `trpc_view` (sync, for WSGI deployments) and
`trpc_view_async` (for ASGI). `urlpatterns()` wires either under a prefix.

## Minimal project wiring

```python
# myapp/trpc.py
from pydantic import BaseModel

from trpc_rs_py import Router


class Post(BaseModel):
    id: int
    title: str


router = Router()


@router.query
def greeting(name: str) -> str:
    return f"Hello {name}!"


@router.mutation
def create_post(title: str) -> Post:
    return Post(id=1, title=title)
```

```python
# mysite/urls.py
from django.contrib import admin
from django.urls import path

from myapp.trpc import router
from trpc_rs_py.django import urlpatterns as trpc_urlpatterns

urlpatterns = [
    path("admin/", admin.site.urls),
    *trpc_urlpatterns(router),          # sync view — /trpc/*
]
```

```bash
python manage.py runserver
```

`trpc_urlpatterns(router, prefix="trpc", asynchronous=False, ...)` returns
`path()` entries and forwards the `production`, `limits`,
`sse_ping_interval`, `max_sse_event_size`, `context_factory`, `middleware`,
and `panel` options — the same kwargs [`mount()`](fastapi.md#options)
takes. `prefix` changes the URL root (`/trpc/posts.by_id` → procedure
`posts.by_id`).

## Views directly

```python
from django.urls import path

from trpc_rs_py.django import trpc_view, trpc_view_async

urlpatterns = [
    path("trpc/<path:trpc_path>", trpc_view(router)),        # sync
    # path("trpc/<path:trpc_path>", trpc_view_async(router))  # async
]
```

The URL converter **must be named `trpc_path`** — the view reads the dotted
procedure path from that parameter. `asgi_view` / `asgi_view_async` are the
generic helpers: they turn any ASGI `http` app into a Django view, but
capture the remainder under the name `path`:

```python
from trpc_rs_py import mount_rest
from trpc_rs_py.django import asgi_view

path("api/<path:path>", asgi_view(mount_rest(router)))   # REST + OpenAPI
```

## Deployment

**WSGI (sync view).** `trpc_view` pumps the ASGI dispatch on a per-request
loop inside a worker thread — the right default under `wsgi.py`:

```bash
gunicorn myproject.wsgi:application -w 4 --threads 8 -b 0.0.0.0:8000
```

Subscriptions return a `StreamingHttpResponse`: **one worker thread per
open stream**, and disconnects are noticed on write — the same caveats as
the [WSGI bridge](flask.md#wsgi-streaming-caveats). Set
`sse_ping_interval` so dead connections surface promptly.

**ASGI (async view).** `trpc_view_async` runs the dispatch on Django's own
event loop — SSE streams with pull-based backpressure and no worker
threads:

```python
urlpatterns = [*trpc_urlpatterns(router, asynchronous=True)]
```

```bash
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000
# or: daphne myproject.asgi:application
```

requires `get_asgi_application()` (Django 3.0+ project layout provides it).

## Django-specific notes

- **Pick the view to match the deployment.** Django also runs async views
  under WSGI (it wraps them itself), but the sync view is cheaper there;
  the sync view under ASGI wastes the loop's advantage.
- **`RequestContext.request` is the `django.http.HttpRequest`** in a
  `context_factory` — full access to `request.user`, sessions, etc.:
  `context_factory=lambda request: {"user": request.request.user}`.
- **Django middleware does not wrap the tRPC dispatch** — authentication
  and auth gates belong in [`context_factory`](../context.md) or
  [tRPC middleware](../middleware.md).
- **Standard `CsrfViewMiddleware` will 403 tRPC POSTs.** The views are
  plain view functions with no `csrf_exempt`; mutations (and batched
  queries, which are POST) fail the check in a default project. Wrap the
  view yourself — `path("trpc/<path:trpc_path>",
  csrf_exempt(trpc_view(router)))` — and for browser-facing, credentialed
  endpoints use [`csrf_protect()`](../csrf.md) and [`cors()`](../cors.md)
  at the adapter level instead of Django's form-oriented middleware.
- The dev playground sits at `/trpc/panel` like every other adapter.

Runnable examples: `examples/django-sync`, `examples/django-async`,
`examples/django-vanilla-ts`.
