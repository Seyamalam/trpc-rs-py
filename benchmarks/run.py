#!/usr/bin/env python3
"""Run the benchmark suite and write raw results (MISSION.md section 10).

Outputs ``benchmarks/results/<version>/{meta,rust-micro,python-micro,http-load}.json``.
Every number is machine-produced by criterion, pytest-benchmark or oha;
never hand-edit the results. Requires: uv, cargo, node, pnpm deps installed
in clients/ts-tests, and oha on PATH (``brew install oha``).

Usage: uv run python benchmarks/run.py [version]
Environment: BENCH_DURATION (default 5s), BENCH_CONNECTIONS (default 16).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DURATION = os.environ.get("BENCH_DURATION", "5s")
CONNECTIONS = os.environ.get("BENCH_CONNECTIONS", "16")
LARGE_MESSAGE = "x" * 100_000


def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, check=True, text=True, **kwargs)  # type: ignore[arg-type]


def command_output(args: list[str]) -> str:
    return run(args, capture_output=True).stdout.strip()


def workspace_version() -> str:
    data = tomllib.loads((ROOT / "Cargo.toml").read_text())
    return str(data["workspace"]["package"]["version"])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(url: str, proc: subprocess.Popen[bytes], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early while waiting for {url}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except urllib.error.HTTPError as error:
            if error.code < 500:
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"server did not become ready: {url}")


def collect_meta(version: str) -> dict[str, object]:
    meta: dict[str, object] = {
        "version": version,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "rust": command_output(["rustc", "--version"]),
        "node": command_output(["node", "--version"]),
        "oha": command_output(["oha", "--version"]),
        "duration_per_workload": DURATION,
        "connections": int(CONNECTIONS),
        "server_workers": 1,
        "warmup": "readiness probe before each server; oha runs after probe",
        "validation": "none on any server (unannotated handlers / no Node input parser)",
    }
    if platform.system() == "Darwin":
        meta["cpu_brand"] = command_output(["sysctl", "-n", "machdep.cpu.brand_string"])
    return meta


def rust_micro(out: Path) -> None:
    run(["cargo", "bench", "-p", "trpc-core", "--bench", "protocol"])
    results: dict[str, dict[str, float]] = {}
    for estimates in sorted((ROOT / "target" / "criterion").glob("*/new/estimates.json")):
        data = json.loads(estimates.read_text())
        results[estimates.parent.parent.name] = {
            "mean_ns": data["mean"]["point_estimate"],
            "median_ns": data["median"]["point_estimate"],
            "stddev_ns": data["std_dev"]["point_estimate"],
        }
    if not results:
        raise RuntimeError("criterion produced no estimates")
    (out / "rust-micro.json").write_text(json.dumps(results, indent=2) + "\n")


def python_micro(out: Path) -> None:
    run(
        [
            "uv",
            "run",
            "pytest",
            "benchmarks/test_bench.py",
            "--benchmark-only",
            f"--benchmark-json={out / 'python-micro.json'}",
            "--benchmark-disable-gc",
            "-p",
            "no:cacheprovider",
        ]
    )


def percent_encode(value: str) -> str:
    return "".join(
        chr(byte) if chr(byte).isalnum() else f"%{byte:02X}" for byte in value.encode()
    )


def oha_run(method: str, url: str, body: str | None, body_file: Path | None) -> dict:
    args = [
        "oha",
        "--no-tui",
        "--output-format",
        "json",
        "-z",
        DURATION,
        "-c",
        CONNECTIONS,
        "-m",
        method,
    ]
    if method == "POST":
        args += ["-H", "content-type: application/json"]
        if body_file is not None:
            args += ["-D", str(body_file)]
        elif body is not None:
            args += ["-d", body]
    args.append(url)
    completed = run(args, capture_output=True)
    return json.loads(completed.stdout)


def http_load(out: Path) -> None:
    single_input = percent_encode(json.dumps({"name": "ada"}))
    batch_input = percent_encode(json.dumps({str(i): {"name": "ada"} for i in range(10)}))
    batch_paths = ",".join(["greet"] * 10)
    mutation_body = json.dumps({"message": "hello"})

    servers: dict[str, tuple[list[str], str, dict[str, tuple[str, str]]]] = {}
    trpc_port = free_port()
    plain_port = free_port()
    node_port = free_port()
    servers["trpc-fastapi"] = (
        [
            "uv", "run", "uvicorn", "benchmarks.apps:trpc_app",
            "--host", "127.0.0.1", "--port", str(trpc_port), "--log-level", "error",
        ],
        f"http://127.0.0.1:{trpc_port}",
        {
            "single_query": ("GET", f"/trpc/greet?input={single_input}"),
            "batch_10": ("GET", f"/trpc/{batch_paths}?batch=1&input={batch_input}"),
            "mutation": ("POST", "/trpc/echo"),
            "large_payload": ("POST", "/trpc/echo"),
        },
    )
    servers["plain-fastapi"] = (
        [
            "uv", "run", "uvicorn", "benchmarks.apps:baseline_app",
            "--host", "127.0.0.1", "--port", str(plain_port), "--log-level", "error",
        ],
        f"http://127.0.0.1:{plain_port}",
        {
            "single_query": ("GET", f"/plain/greet?input={single_input}"),
            "batch_10": ("GET", f"/plain/batch?input={batch_input}"),
            "mutation": ("POST", "/plain/echo"),
            "large_payload": ("POST", "/plain/echo"),
        },
    )
    servers["node-trpc"] = (
        ["node", "clients/ts-tests/bench/node-reference.mjs", str(node_port)],
        f"http://127.0.0.1:{node_port}",
        {
            "single_query": ("GET", f"/greet?input={single_input}"),
            "batch_10": ("GET", f"/{batch_paths}?batch=1&input={batch_input}"),
            "mutation": ("POST", "/echo"),
            "large_payload": ("POST", "/echo"),
        },
    )

    results: dict[str, dict[str, dict]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        large_file = Path(tmp) / "large.json"
        large_file.write_text(json.dumps({"message": LARGE_MESSAGE}))
        procs: dict[str, subprocess.Popen[bytes]] = {}
        try:
            for name, (args, base, workloads) in servers.items():
                proc = subprocess.Popen(
                    args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                procs[name] = proc
                probe_method, probe_path = workloads["single_query"]
                wait_ready(base + probe_path, proc)
                results[name] = {}
                for workload, (method, path) in workloads.items():
                    print(f"http: {name}/{workload}", flush=True)
                    body = mutation_body if workload == "mutation" else None
                    body_file = large_file if workload == "large_payload" else None
                    results[name][workload] = oha_run(method, base + path, body, body_file)
        finally:
            for proc in procs.values():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    (out / "http-load.json").write_text(json.dumps(results, indent=2) + "\n")


def main() -> int:
    if shutil.which("oha") is None:
        print("run: oha is required (brew install oha)", file=sys.stderr)
        return 1
    version = sys.argv[1] if len(sys.argv) > 1 else workspace_version()
    out = ROOT / "benchmarks" / "results" / version
    out.mkdir(parents=True, exist_ok=True)
    (out / "meta.json").write_text(json.dumps(collect_meta(version), indent=2) + "\n")
    print(f"run: results -> {out}", flush=True)
    rust_micro(out)
    python_micro(out)
    http_load(out)
    print("run: done (meta, rust-micro, python-micro, http-load)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())