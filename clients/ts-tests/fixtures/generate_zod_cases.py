"""Regenerate `fixtures/zod-cases.json` — hypothesis-generated parity cases.

Each case is a JSON value plus the verdict the Pydantic model gives it; the
TypeScript side (`src/zod-parity.test.ts`) runs the same value through the
generated zod schema and asserts the same accept/reject verdict — the
`hypothesis` parity check MISSION §2.5 requires, run end to end.

Deterministic: `derandomize=True` plus `database=None`, so committed output
is byte-stable for a given strategy set. Run:

    uv run python clients/ts-tests/fixtures/generate_zod_cases.py
"""

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st
from pydantic import BaseModel, TypeAdapter  # noqa: E402
from router import SignupForm  # noqa: E402

_TARGET = _HERE / "zod-cases.json"
_MAX_CASES = 60

# JSON-native value pools per field, mixing in-range and out-of-range so the
# case set exercises both accept and reject paths on every constraint.
_usernames = st.one_of(
    st.from_regex(r"\A[a-z][a-z0-9_]{0,15}\Z", fullmatch=True),
    st.text(alphabet="ab_\t", min_size=0, max_size=24),
    st.none(),
    st.lists(st.text(max_size=3), max_size=2),
)
_ages = st.one_of(
    st.integers(0, 200),
    st.none(),
    st.lists(st.integers(max_value=5), max_size=1),
)
_nicknames = st.one_of(st.none(), st.text(max_size=20), st.integers())
_tags = st.one_of(
    st.lists(st.text(max_size=8), max_size=5),
    st.none(),
    st.text(max_size=4),
)
_signup_forms = st.fixed_dictionaries(
    {
        "username": _usernames,
        "age": _ages,
    },
    optional={"nickname": _nicknames, "tags": _tags},
)
_inputs = st.one_of(
    st.fixed_dictionaries({"form": _signup_forms}),
    _signup_forms,  # wrong shape: model object where {form: ...} is expected
    st.none(),
    st.lists(_signup_forms, max_size=2),
)

# Mostly-valid pool: every field strategy stays inside its constraints, so
# most of these cases carry expect=true and exercise the accept path.
_valid_forms = st.fixed_dictionaries(
    {
        "username": st.from_regex(r"\A[a-z][a-z0-9_]{2,15}\Z", fullmatch=True),
        "age": st.integers(13, 120),
    },
    optional={
        "nickname": st.one_of(st.none(), st.text(max_size=20)),
        "tags": st.lists(st.text(max_size=8), max_size=3),
    },
)
_valid_inputs = st.fixed_dictionaries({"form": _valid_forms})


class _SignupInput(BaseModel):
    """Synthetic model matching the `signup` procedure's input object."""

    form: SignupForm


def _verdict(value: object) -> bool:
    """Return whether Pydantic accepts `value` as the signup input object."""
    try:
        TypeAdapter(_SignupInput).validate_python(value)
    except Exception:
        return False
    return True


def _collect() -> list[dict[str, object]]:
    """Generate the deterministic case list (valid pool first, then mixed)."""
    cases: list[dict[str, object]] = []

    def runner(strategy: st.SearchStrategy[object], limit: int) -> None:
        @settings(
            derandomize=True,
            database=None,
            max_examples=limit,
            suppress_health_check=list(HealthCheck),
        )
        @given(strategy)
        def record(value: object) -> None:
            cases.append({"schema": "SignupInput", "value": value, "expect": _verdict(value)})

        record()

    runner(_valid_inputs, _MAX_CASES // 2)
    runner(_inputs, _MAX_CASES // 2)
    return cases


def main() -> None:
    """Write the regenerated parity cases."""
    cases = _collect()
    accepted = sum(1 for case in cases if case["expect"])
    _TARGET.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"wrote {_TARGET} ({len(cases)} cases, {accepted} accept)")


if __name__ == "__main__":
    main()
