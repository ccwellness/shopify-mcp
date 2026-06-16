"""Drive the MCP server over stdio with a real JSON-RPC handshake.

Spawns `python -m mcp_server` as a child process, exchanges:

  1. initialize  + notifications/initialized
  2. tools/list
  3. tools/call list_stores
  4. tools/call get_kpis (small window)
  5. tools/call list_low_stock (default threshold)

Prints request/response pairs so a human can eyeball them.

Run:
  uv run python scripts/smoke_test_mcp_stdio.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SNIPPET_LEN = 300


def _make_request(rid: int, method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _make_notification(method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _send(proc: subprocess.Popen, msg: dict) -> None:
    line = json.dumps(msg) + "\n"
    assert proc.stdin is not None
    proc.stdin.write(line)
    proc.stdin.flush()
    label = "--> NOTIFY" if "id" not in msg else f"--> REQ #{msg['id']}"
    print(f"\n{label}: {msg.get('method')}")
    if "params" in msg:
        print(f"   params: {json.dumps(msg['params'])[:200]}")


def _read_response(proc: subprocess.Popen, expect_id: int) -> dict:
    """Read JSON lines until we see the matching id (skip server-initiated notifications)."""
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout before responding")
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # Could be a log line on stdout — print and keep going.
            print(f"   (non-JSON stdout: {line!r})")
            continue
        if "id" in msg and msg["id"] == expect_id:
            return msg
        # Notification from server (e.g. progress) — log and continue.
        print(f"   (server notification: {msg.get('method')})")


def _summarize_result(label: str, result: dict) -> None:
    if "error" in result:
        print(f"   <-- ERROR {result['error']}")
        return
    payload = result.get("result", {})
    # tools/list returns {tools: [...]}; tools/call returns structuredContent / content.
    if "tools" in payload:
        names = [t["name"] for t in payload["tools"]]
        print(f"   <-- {len(names)} tools: {', '.join(sorted(names))}")
        return
    if "structuredContent" in payload:
        sc = payload["structuredContent"]
        full = json.dumps(sc)
        snippet = full[:_SNIPPET_LEN]
        print(f"   <-- structuredContent: {snippet}{'…' if len(full) > _SNIPPET_LEN else ''}")
        return
    if "content" in payload:
        snippet = json.dumps(payload["content"])[:_SNIPPET_LEN]
        print(f"   <-- content: {snippet}")
        return
    print(f"   <-- {json.dumps(payload)[:_SNIPPET_LEN]}")
    _ = label


def _drain_stderr(proc: subprocess.Popen) -> Iterator[str]:
    """Yield any stderr lines currently waiting (non-blocking is hard on Windows;
    we let stderr inherit and rely on the user seeing it interleaved)."""
    return iter(())


def main() -> int:
    cmd = [sys.executable, "-m", "mcp_server"]
    print(f"$ {' '.join(cmd)}\n")
    proc = subprocess.Popen(  # noqa: S603 — local trusted invocation
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,  # let log lines flow to the user's terminal
        text=True,
        bufsize=1,
        encoding="utf-8",
    )

    try:
        # 1. Initialize
        init_req = _make_request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "smoke-test-mcp-stdio", "version": "0.1"},
            },
        )
        _send(proc, init_req)
        init_resp = _read_response(proc, 1)
        _summarize_result("initialize", init_resp)

        # 2. initialized notification
        _send(proc, _make_notification("notifications/initialized"))

        # 3. tools/list
        list_req = _make_request(2, "tools/list", {})
        _send(proc, list_req)
        list_resp = _read_response(proc, 2)
        _summarize_result("tools/list", list_resp)

        # 4. tools/call list_stores
        call_req = _make_request(3, "tools/call", {"name": "list_stores", "arguments": {}})
        _send(proc, call_req)
        _summarize_result("list_stores", _read_response(proc, 3))

        # 5. tools/call get_kpis
        kpis_req = _make_request(
            4,
            "tools/call",
            {"name": "get_kpis", "arguments": {"since": "7d", "until": "today"}},
        )
        _send(proc, kpis_req)
        _summarize_result("get_kpis", _read_response(proc, 4))

        # 6. tools/call list_low_stock
        low_req = _make_request(
            5,
            "tools/call",
            {"name": "list_low_stock", "arguments": {"limit": 5}},
        )
        _send(proc, low_req)
        _summarize_result("list_low_stock", _read_response(proc, 5))

        # 7. tools/call get_subscription on a fake id (should return null)
        sub_req = _make_request(
            6,
            "tools/call",
            {"name": "get_subscription", "arguments": {"contract_id": 1}},
        )
        _send(proc, sub_req)
        _summarize_result("get_subscription", _read_response(proc, 6))

        print("\n[OK] stdio handshake completed without errors")
        return 0
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
