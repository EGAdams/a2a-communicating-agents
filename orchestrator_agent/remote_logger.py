"""Remote logger client for orchestrator_agent."""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("LETTA_LOGGER_API", "http://100.80.49.10:8284/libraries/local-php-api")
DASHBOARD_URL = os.environ.get("A2A_DASHBOARD_URL", "http://localhost:3000/api/agent-log")

RUNNING_COLOR = "lightyellow"
PASS_COLOR = "lightgreen"
FAIL_COLOR = "#fb6666"
MAX_LOG_ENTRIES = 120
MAX_MESSAGE_CHARS = 800
MAX_OBJECT_DATA_BYTES = 200_000
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def default_led() -> Dict[str, Any]:
    return {
        "classObject": {
            "background_color": RUNNING_COLOR,
            "text_align": "left",
            "margin_top": "2px",
            "color": "black",
        },
        "ledText": "ready.",
        "RUNNING_COLOR": RUNNING_COLOR,
        "PASS_COLOR": PASS_COLOR,
        "FAIL_COLOR": FAIL_COLOR,
    }


def updated_led(message: str, current: Dict[str, Any]) -> Dict[str, Any]:
    cls = dict(default_led()["classObject"])
    cls.update((current.get("classObject") or {}))
    led = dict(current)
    led["classObject"] = cls
    led["ledText"] = message

    if message.lower().startswith("idle heartbeat:"):
        return led

    is_timeout_like = (
        re.search(r"timed?\s*out", message, re.IGNORECASE)
        or re.search(r"\btimeout\b", message, re.IGNORECASE)
        or re.search(r"\bhung\b", message, re.IGNORECASE)
        or re.search(r"\babort(?:ed|error)?\b", message, re.IGNORECASE)
    )

    if (
        re.search(r"\bfinished\b", message, re.IGNORECASE)
        or re.search(r"\bPASS(?:ED)?\b", message, re.IGNORECASE)
        or re.search(r"test complete", message, re.IGNORECASE)
    ):
        led["classObject"]["background_color"] = PASS_COLOR
        led["classObject"]["color"] = "black"
    elif "ERROR" in message or re.search(r"\bFAIL(?:ED)?\b", message) or is_timeout_like:
        led["classObject"]["background_color"] = FAIL_COLOR
        led["classObject"]["color"] = "white"
    else:
        led["classObject"]["background_color"] = RUNNING_COLOR
        led["classObject"]["color"] = "black"

    return led


def sanitize_log_message(message: str) -> str:
    out = ANSI_ESCAPE_PATTERN.sub("", message)
    out = out.replace("'", "\u2019").replace('"', "\u201d")
    out = re.sub(r"[\r\t]+", " ", out)
    out = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", " ", out)
    out = re.sub(r" {2,}", " ", out)
    out = re.sub(r"\n+", " | ", out)
    return out.strip()


def http_json(url: str, method: str, payload: Optional[Dict[str, Any]], timeout_sec: float) -> Tuple[int, str]:
    data = None
    headers = {}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url=url, method=method, data=data, headers=headers)
    with urlopen(req, timeout=timeout_sec) as res:
        body = res.read().decode("utf-8", errors="replace")
        return int(res.status), body


@dataclass
class RemoteLogger:
    object_view_id: str

    def __post_init__(self) -> None:
        self.log_objects: List[Dict[str, Any]] = []
        self.monitor_led: Dict[str, Any] = default_led()

    def init(self) -> None:
        existing = self._fetch_existing_state()
        if existing is not None:
            self.log_objects = existing.get("logObjects", [])
            raw_led = existing.get("monitorLed") or default_led()
            if raw_led.get("classObject") is None:
                raw_led = {**raw_led, "classObject": default_led()["classObject"]}
                self.monitor_led = raw_led
                self._post("update")
            else:
                self.monitor_led = raw_led
            return

        self._post("insert")

    def log(self, message: str) -> None:
        safe_message = sanitize_log_message(str(message)[:MAX_MESSAGE_CHARS])
        timestamp = int(time.time() * 1000)
        rid = random.randint(0, 10**13 - 1)

        self.log_objects.append(
            {
                "timestamp": timestamp,
                "id": f"{self.object_view_id}_{rid}_{timestamp}",
                "message": safe_message,
                "method": "createLogObject",
            }
        )
        self._shrink_state_for_transport()
        self.monitor_led = updated_led(safe_message, self.monitor_led)
        self._post("update")
        self._notify_dashboard(safe_message, timestamp)

    def clear_logs(self, led_text: str = "ready.") -> None:
        self.monitor_led = {**default_led(), "ledText": led_text}
        self._post("update")

    def flush_logs(self, led_text: str = "ready.") -> None:
        self.log_objects = []
        self.monitor_led = {**default_led(), "ledText": led_text}
        self._post("update")

    def _state(self) -> Dict[str, Any]:
        return {
            "object_view_id": self.object_view_id,
            "logObjects": self.log_objects,
            "monitorLed": self.monitor_led,
        }

    def _serialized_state(self) -> str:
        self._shrink_state_for_transport()
        return json.dumps(self._state(), separators=(",", ":"))

    def _shrink_state_for_transport(self) -> None:
        if len(self.log_objects) > MAX_LOG_ENTRIES:
            self.log_objects = self.log_objects[-MAX_LOG_ENTRIES:]

        serialized = json.dumps(self._state(), separators=(",", ":"))
        while len(serialized.encode("utf-8")) > MAX_OBJECT_DATA_BYTES and len(self.log_objects) > 1:
            self.log_objects.pop(0)
            serialized = json.dumps(self._state(), separators=(",", ":"))

    def _notify_dashboard(self, message: str, timestamp: int) -> None:
        """Fire-and-forget push to local dashboard SSE stream. Never raises."""
        try:
            led_color = (self.monitor_led.get("classObject") or {}).get("background_color", RUNNING_COLOR)
            payload = {
                "agent_id": self.object_view_id,
                "message": message,
                "timestamp": timestamp,
                "led_color": led_color,
            }
            http_json(DASHBOARD_URL, method="POST", payload=payload, timeout_sec=1.0)
        except Exception:
            pass  # Never let dashboard notification break agent logging

    def _post(self, action: str, timeout_sec: float = 8.0) -> None:
        payload = {
            "object_view_id": self.object_view_id,
            "object_data": self._serialized_state(),
        }

        try:
            status, body_text = http_json(
                f"{BASE_URL}/object/{action}",
                method="POST",
                payload=payload,
                timeout_sec=timeout_sec,
            )
        except Exception as err:
            if self._is_state_persisted():
                return
            raise RuntimeError(f"[RemoteLogger] {action} failed before response: {err}") from err

        if status < 200 or status >= 300:
            if action == "update" and self._try_insert_fallback(timeout_sec):
                return
            if self._is_state_persisted_with_retry():
                return
            raise RuntimeError(f"[RemoteLogger] {action} failed (HTTP {status})")

        try:
            body = json.loads(body_text) if body_text else None
        except Exception as err:
            if self._is_state_persisted_with_retry():
                return
            raise RuntimeError(f"[RemoteLogger] {action} failed (invalid JSON response): {err}") from err

        if isinstance(body, dict) and body.get("error"):
            if action == "update" and self._try_insert_fallback(timeout_sec):
                return
            if self._is_state_persisted_with_retry():
                return
            raise RuntimeError(f"[RemoteLogger] {action} error: {json.dumps(body)}")

    def _try_insert_fallback(self, timeout_sec: float = 8.0) -> bool:
        try:
            status, _ = http_json(
                f"{BASE_URL}/object/insert",
                method="POST",
                payload={
                    "object_view_id": self.object_view_id,
                    "object_data": self._serialized_state(),
                },
                timeout_sec=timeout_sec,
            )
            if status < 200 or status >= 300:
                return False
            return self._is_state_persisted()
        except Exception:
            return False

    def _fetch_existing_state(self) -> Optional[Dict[str, Any]]:
        try:
            status, body_text = http_json(
                f"{BASE_URL}/object/select?object_view_id={quote(self.object_view_id)}",
                method="GET",
                payload=None,
                timeout_sec=8.0,
            )
            if status < 200 or status >= 300:
                return None
            body = json.loads(body_text) if body_text else None
            return self._parse_select_payload(body)
        except Exception:
            return None

    def _parse_select_payload(self, body: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(body, dict) or body.get("error") or not body.get("object_data"):
            return None
        try:
            parsed = json.loads(body["object_data"])
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception:
            return None

    def _is_state_persisted(self) -> bool:
        persisted = self._fetch_existing_state()
        if not persisted or persisted.get("object_view_id") != self.object_view_id:
            return False

        if not self.log_objects:
            return True

        expected_last = self.log_objects[-1].get("id")
        persisted_entries = persisted.get("logObjects") or []
        return any(entry.get("id") == expected_last for entry in persisted_entries if isinstance(entry, dict))

    def _is_state_persisted_with_retry(self, attempts: int = 4, delay_sec: float = 0.25) -> bool:
        for index in range(attempts):
            if self._is_state_persisted():
                return True
            if index < attempts - 1:
                time.sleep(delay_sec)
        return False
