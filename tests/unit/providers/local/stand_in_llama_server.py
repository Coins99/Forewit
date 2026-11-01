"""A stand-in for llama-server in tests, run as its own process (standard library only).

It takes the flags Forewit passes and answers as llama-server b12015 does: /health and
/v1/health for everyone, everything else only with the API key (HTTP 401 otherwise), and a
streamed /v1/chat/completions with one result per message key, quoting each body's start.
The "model file" (-m) is a JSON scenario:

- "print": lines to print first; "exit": then exit with this code, serving nothing;
- "bind_delay": seconds to wait before binding the port;
- "loading_seconds": how long /health answers 503 "Loading model";
- "open": accept any key, like a server started without --api-key;
- "slow_first_props": seconds to wait before answering the first /props;
- "crash_on_chat": exit when a chat request arrives;
- "status": answer chat requests with this HTTP status instead.
"""

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # A port another program holds must fail, as it does for C++.
    daemon_threads = True


def _result(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_key": message["message_key"],
        "category": "information",
        "summary": "A stand-in summary.",
        "action_required": False,
        "action_text": None,
        "deadline_text": None,
        "deadline_date": None,
        "deadline_time": None,
        "stated_timezone": None,
        "confidence": 0.5,
        "evidence": str(message["body"])[:24],
        "actions": [],
        "follow_up": "none",
        "follow_up_evidence": None,
    }


def handler(api_key: str | None, scenario: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    ready_at = time.monotonic() + float(scenario.get("loading_seconds", 0))
    slow = [float(scenario.get("slow_first_props", 0))]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # llama-server's request lines aren't needed here.

        def _json(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if scenario.get("open") or api_key is None:
                return True
            if self.headers.get("Authorization") == f"Bearer {api_key}":
                return True
            self._json(401, {"error": {"code": 401, "message": "Invalid API Key"}})
            return False

        def do_GET(self) -> None:
            if self.path in ("/health", "/v1/health"):
                if time.monotonic() < ready_at:
                    self._json(503, {"error": {"code": 503, "message": "Loading model"}})
                else:
                    self._json(200, {"status": "ok"})
            elif self._authorized():
                if self.path == "/props":
                    time.sleep(slow.pop() if slow else 0)
                    self._json(200, {"total_slots": 1})
                else:
                    self._json(404, {"error": {"code": 404, "message": "File Not Found"}})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path != "/v1/chat/completions" or "messages" not in body:
                self._json(400, {"error": {"code": 400, "message": "bad request"}})
                return
            if scenario.get("crash_on_chat"):
                os._exit(3)
            if "status" in scenario:
                self._json(int(scenario["status"]), {"error": {"message": "stand-in failure"}})
                return
            messages = json.loads(body["messages"][1]["content"])["messages"]
            content = json.dumps({"results": [_result(message) for message in messages]})
            chunks = [
                {"choices": [{"delta": {"content": content}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("-m", dest="model", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--api-key", dest="api_key")
    args, _ = parser.parse_known_args()
    scenario = json.loads(Path(args.model).read_text(encoding="utf-8"))
    for line in scenario.get("print", []):
        print(line, flush=True)
    if "exit" in scenario:
        sys.exit(int(scenario["exit"]))
    time.sleep(float(scenario.get("bind_delay", 0)))
    try:
        server = Server((args.host, args.port), handler(args.api_key, scenario))
    except OSError:
        print("E srv start: couldn't bind HTTP server socket", flush=True)
        sys.exit(1)
    print(f"I srv listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
