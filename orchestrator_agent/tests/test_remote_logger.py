import json

from remote_logger import RemoteLogger, updated_led


def test_remote_logger_init_and_log_updates_state(monkeypatch):
    calls = []

    def fake_http_json(url, method, payload, timeout_sec):
        calls.append(
            {
                "url": url,
                "method": method,
                "payload": payload,
                "timeout_sec": timeout_sec,
            }
        )
        if url.endswith("/object/select?object_view_id=OrchestratorAgent_2026"):
            return 404, '{"error":"missing"}'
        return 200, '{"ok":true}'

    monkeypatch.setattr("remote_logger.http_json", fake_http_json)

    logger = RemoteLogger("OrchestratorAgent_2026")
    logger.init()
    logger.log("build finished with PASS, but with ANSI \x1b[31mred\x1b[0m and \"quotes\"")

    assert [call["method"] for call in calls[:2]] == ["GET", "POST"]
    insert_payload = calls[1]["payload"]
    assert insert_payload["object_view_id"] == "OrchestratorAgent_2026"

    update_payload = calls[2]["payload"]
    serialized_state = json.loads(update_payload["object_data"])
    assert serialized_state["monitorLed"]["classObject"]["background_color"] == "lightgreen"
    assert "ANSI" in serialized_state["logObjects"][-1]["message"]
    assert "\x1b" not in serialized_state["logObjects"][-1]["message"]
    assert '"' not in serialized_state["logObjects"][-1]["message"]


def test_idle_heartbeat_preserves_finished_led_state():
    led = updated_led("Finished processing message_id=abc", {})
    led = updated_led("Idle heartbeat: no incoming messages for 30s", led)

    assert led["classObject"]["background_color"] == "lightgreen"
    assert led["ledText"] == "Idle heartbeat: no incoming messages for 30s"
