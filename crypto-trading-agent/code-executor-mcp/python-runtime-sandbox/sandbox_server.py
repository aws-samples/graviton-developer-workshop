"""
Sandbox HTTP Server

A simple HTTP server running inside the gVisor sandbox container.
Provides endpoints for file upload/download and command execution.
This is the runtime that the k8s-agent-sandbox SDK communicates with.
"""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

WORKING_DIR = "/app"


class SandboxHandler(BaseHTTPRequestHandler):
    """HTTP handler for sandbox file and execution operations."""

    def do_POST(self):
        """Handle POST requests for /upload and /exec endpoints."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/upload":
            self._handle_upload(body)
        elif self.path in ("/exec", "/execute"):
            self._handle_exec(body)
        else:
            self._send_response(404, {"error": f"Unknown endpoint: {self.path}"})

    def do_GET(self):
        """Handle GET requests for /download and /health endpoints."""
        if self.path == "/health":
            self._send_response(200, {"status": "healthy"})
        elif self.path.startswith("/download"):
            self._handle_download()
        else:
            self._send_response(404, {"error": f"Unknown endpoint: {self.path}"})

    def _handle_upload(self, body: bytes):
        """
        Handle file upload requests.

        Expects JSON body with:
            - path: absolute file path to write to
            - content: base64-encoded or UTF-8 file content
            - encoding: 'base64' or 'utf-8' (default: 'utf-8')
        """
        try:
            request = json.loads(body)
            file_path = request["path"]
            content = request["content"]
            encoding = request.get("encoding", "utf-8")

            # Ensure parent directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            if encoding == "base64":
                import base64
                file_content = base64.b64decode(content)
                with open(file_path, "wb") as f:
                    f.write(file_content)
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

            self._send_response(200, {
                "status": "ok",
                "path": file_path,
                "size": os.path.getsize(file_path),
            })

        except Exception as e:
            self._send_response(500, {"error": str(e)})

    def _handle_download(self):
        """
        Handle file download requests.

        Expects query parameter: ?path=/app/chart.png
        Returns the file content as binary with appropriate content type.
        """
        try:
            # Parse path from query string
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            file_path = params.get("path", [None])[0]

            if not file_path:
                self._send_response(400, {"error": "Missing 'path' query parameter"})
                return

            if not os.path.exists(file_path):
                self._send_response(404, {"error": f"File not found: {file_path}"})
                return

            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        except Exception as e:
            self._send_response(500, {"error": str(e)})

    def _handle_exec(self, body: bytes):
        """
        Handle command execution requests.

        Expects JSON body with:
            - command: list of strings (e.g., ["python3", "/app/main.py"])
            - timeout: execution timeout in seconds (default: 120)
            - working_dir: working directory (default: /app)
        """
        try:
            request = json.loads(body)
            command = request["command"]
            timeout = request.get("timeout", 120)
            working_dir = request.get("working_dir", WORKING_DIR)

            # Handle both string and list commands
            if isinstance(command, str):
                import shlex
                command = shlex.split(command)

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=working_dir,
                env={
                    **os.environ,
                    "MPLBACKEND": "Agg",  # Non-interactive matplotlib backend
                },
            )

            self._send_response(200, {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            })

        except subprocess.TimeoutExpired:
            self._send_response(200, {
                "stdout": "",
                "stderr": f"Execution timed out after {timeout} seconds",
                "exit_code": 124,
            })
        except Exception as e:
            self._send_response(500, {"error": str(e)})

    def _send_response(self, status_code: int, body: dict):
        """Send a JSON response."""
        response_body = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format, *args):
        """Override to use standard logging format."""
        sys.stderr.write(f"[sandbox] {args[0]}\n")


def main():
    """Start the sandbox HTTP server on port 8888."""
    port = int(os.environ.get("SANDBOX_PORT", "8888"))
    server = HTTPServer(("0.0.0.0", port), SandboxHandler)
    print(f"Sandbox server running on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
