"""Entrada por arquivo ou webhook HTTP."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import sys

from .analysis import analyze, render_markdown
from .llm import suggest

MAX_BODY = 65536


def process(payload: dict, use_ai: bool = False, model: str = "gemma3:1b") -> dict:
    report = analyze(payload)
    if use_ai:
        try:
            report["ai_suggestion"] = suggest(report, model=model)
        except (OSError, ValueError, KeyError, TimeoutError) as exc:
            report["ai_error"] = f"Ollama indisponível ({type(exc).__name__}); triagem por regras preservada."
    return report


def make_handler(token: str, use_ai: bool = False, model: str = "gemma3:1b"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/webhook/alertmanager":
                return self._send(404, {"error": "not found"})
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {token}"):
                return self._send(401, {"error": "unauthorized"})
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 < length <= MAX_BODY:
                    return self._send(413, {"error": "body exceeds 64 KiB or length missing"})
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    return self._send(415, {"error": "expected application/json"})
                payload = json.loads(self.rfile.read(length))
                report = process(payload, use_ai=use_ai, model=model)
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "invalid alert payload"})
            self._send(200, report)

        def _send(self, code: int, body: dict):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args):
            # Sem corpo, token, caminho com query ou dados de alertas nos logs.
            sys.stderr.write(f"http status={args[1]}\n")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Triagem conservadora de alertas do Alertmanager")
    sub = parser.add_subparsers(dest="command", required=True)
    file_cmd = sub.add_parser("analyze", help="analisa um arquivo JSON local")
    file_cmd.add_argument("file", type=Path)
    file_cmd.add_argument("--json", action="store_true", help="saída estruturada")
    file_cmd.add_argument("--ollama", action="store_true", help="modelo local opcional")
    file_cmd.add_argument("--model", default="gemma3:1b")
    server_cmd = sub.add_parser("serve", help="recebe webhook autenticado")
    server_cmd.add_argument("--host", default="127.0.0.1")
    server_cmd.add_argument("--port", type=int, default=8080)
    server_cmd.add_argument("--ollama", action="store_true")
    server_cmd.add_argument("--model", default="gemma3:1b")
    args = parser.parse_args(argv)
    if args.command == "analyze":
        try:
            report = process(json.loads(args.file.read_text(encoding="utf-8")), args.ollama, args.model)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_markdown(report))
        return 0
    token = os.environ.get("INCIDENT_COPILOT_TOKEN", "")
    if len(token) < 24:
        parser.error("Defina INCIDENT_COPILOT_TOKEN com pelo menos 24 caracteres.")
    with ThreadingHTTPServer((args.host, args.port), make_handler(token, args.ollama, args.model)) as server:
        print(f"Recebendo alertas em {args.host}:{args.port}", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

