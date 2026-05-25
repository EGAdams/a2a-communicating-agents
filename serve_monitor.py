#!/usr/bin/env python3
"""
Minimal HTTP server for monitor_thoughts_plan.html.
Serves the file over plain HTTP so browsers allow ws:// connections.

Usage:
    python3 serve_monitor.py
    Then open: http://100.72.158.63:8899/monitor
"""

import http.server
import os
import sys

PORT = 8899
TAILSCALE_IP = "100.72.158.63"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(SCRIPT_DIR, "monitor_thoughts_plan.html")


class MonitorHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/monitor", "/monitor.html", "/", "/monitor_thoughts_plan.html"):
            try:
                with open(HTML_FILE, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, "monitor_thoughts_plan.html not found")
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        print(f"[HTTP] {self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    os.chdir(SCRIPT_DIR)
    server = http.server.HTTPServer(("0.0.0.0", PORT), MonitorHandler)
    print(f"Monitor HTTP server listening on 0.0.0.0:{PORT}")
    print(f"  Open: http://{TAILSCALE_IP}:{PORT}/monitor")
    print(f"  File: {HTML_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
