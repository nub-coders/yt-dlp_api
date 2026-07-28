"""Zero-dependency Prometheus metrics.

In-process counters, rendered in Prometheus text exposition format. Per-process
state is correct for Prometheus — it scrapes each replica as its own target, so
no cross-replica sharing (unlike job state, which lives in Redis).

ponytail: summary-style latency (_sum/_count → average) instead of full
histograms. Add buckets only when you actually need p95/p99 on the scrape side.
"""
from collections import defaultdict

_req_total: dict = defaultdict(int)      # (method, path, status) -> count
_latency_sum: dict = defaultdict(float)  # (method, path) -> seconds
_latency_count: dict = defaultdict(int)  # (method, path) -> count


def record(method: str, path: str, status: int, duration: float) -> None:
    _req_total[(method, path, str(status))] += 1
    _latency_sum[(method, path)] += duration
    _latency_count[(method, path)] += 1


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


def render() -> str:
    lines = [
        "# HELP http_requests_total Total HTTP requests by method, path and status.",
        "# TYPE http_requests_total counter",
    ]
    for (method, path, status), n in sorted(_req_total.items()):
        lines.append(
            f'http_requests_total{{method="{_esc(method)}",path="{_esc(path)}",status="{status}"}} {n}'
        )
    lines += [
        "# HELP http_request_duration_seconds Request latency by method and path.",
        "# TYPE http_request_duration_seconds summary",
    ]
    for (method, path), total in sorted(_latency_sum.items()):
        labels = f'method="{_esc(method)}",path="{_esc(path)}"'
        lines.append(f"http_request_duration_seconds_sum{{{labels}}} {total}")
        lines.append(f"http_request_duration_seconds_count{{{labels}}} {_latency_count[(method, path)]}")
    return "\n".join(lines) + "\n"
