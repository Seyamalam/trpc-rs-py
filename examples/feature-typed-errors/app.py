"""feature-typed-errors: declared `errors=[...]` with typed data payloads.

Procedures declare the `TRPCError` subclasses they can raise; each class
carries a `code` and a Pydantic `data_model`. The wire envelope merges the
payload fields into `error.data` next to `code`/`httpStatus`/`path`, and the
generated TypeScript exposes a discriminated union so clients can `switch`
on `error.data.code` with fully typed fields (ADR 0009).

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/feature-typed-errors && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then call it with the official `@trpc/client` (see README.md) at
`http://127.0.0.1:8000/trpc`.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount


class OrderRef(BaseModel):
    """Typed payload carried by `OrderNotFound` errors."""

    order_id: int


class OrderNotFound(TRPCError):
    """Typed error raised when an order id is unknown."""

    code = TRPCErrorCode.NOT_FOUND
    data_model = OrderRef


class RateLimitInfo(BaseModel):
    """Typed payload carried by `RateLimited` errors."""

    retry_after: float
    limit: int


class RateLimited(TRPCError):
    """Typed error raised when an order was already placed."""

    code = TRPCErrorCode.TOO_MANY_REQUESTS
    data_model = RateLimitInfo


class Order(BaseModel):
    """An order returned by the order procedures."""

    id: int
    item: str
    quantity: int


_ORDERS: dict[int, Order] = {
    1: Order(id=1, item="book", quantity=2),
    2: Order(id=2, item="pen", quantity=5),
}
# Orders already checked out in this process; resubmitting one is
# rate-limited (a stand-in for a real per-order attempt limit).
_PLACED: set[int] = set()


def build_router() -> Router:
    """Build the example tRPC router (also used to generate the TS types)."""
    router = Router()

    @router.query(errors=[OrderNotFound])
    def order_by_id(order_id: int) -> Order:
        """Fetch an order; raises the declared typed error for unknown ids."""
        order = _ORDERS.get(order_id)
        if order is None:
            raise OrderNotFound(
                f"Order {order_id} not found", data=OrderRef(order_id=order_id)
            )
        return order

    @router.mutation(errors=[OrderNotFound, RateLimited])
    def place_order(order_id: int) -> Order:
        """Check out an order once; resubmits raise the `RateLimited` error."""
        order = _ORDERS.get(order_id)
        if order is None:
            raise OrderNotFound(
                f"Order {order_id} not found", data=OrderRef(order_id=order_id)
            )
        if order_id in _PLACED:
            raise RateLimited(
                f"Order {order_id} is already being checked out",
                data=RateLimitInfo(retry_after=30.0, limit=1),
            )
        _PLACED.add(order_id)
        return order

    return router


def build_app() -> FastAPI:
    """Build the example FastAPI app with the tRPC router mounted at /trpc."""
    app = FastAPI()
    app.mount("/trpc", mount(build_router()))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        log_level="error",
    )
