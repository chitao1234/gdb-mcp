"""Pure helpers for parsing GDB/MI responses."""

from typing import Any, Mapping

from .mi_models import ParsedMiResponse


def parse_mi_responses(responses: list[dict[str, Any]]) -> ParsedMiResponse:
    """Parse raw pygdbmi response dictionaries into a normalized structure."""

    parsed = ParsedMiResponse()

    for response in responses:
        msg_type = response.get("type")

        if msg_type == "console":
            parsed.console.append(response.get("payload"))
        elif msg_type == "log":
            parsed.log.append(response.get("payload"))
        elif msg_type == "output":
            parsed.output.append(response.get("payload"))
        elif msg_type == "result":
            parsed.result = response.get("payload")
            message = response.get("message")
            parsed.result_class = message if isinstance(message, str) else None
        elif msg_type == "notify":
            payload = response.get("payload")
            notify_record: dict[str, Any]
            if isinstance(payload, dict):
                notify_record = dict(payload)
            else:
                notify_record = {"payload": payload}

            notify_record["message"] = response.get("message")
            parsed.notify.append(notify_record)

    return parsed


def extract_mi_result_payload(command_result: Mapping[str, Any]) -> Any | None:
    """Extract the MI result payload from the current command result shape."""

    if "status" in command_result:
        if command_result.get("status") != "success":
            return None

        result_container = command_result.get("result")
        if not isinstance(result_container, Mapping):
            return None

        return result_container.get("result")

    return command_result.get("result")
