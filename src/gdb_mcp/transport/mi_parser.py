"""Pure helpers for parsing GDB/MI responses."""

from collections.abc import Mapping
from typing import cast

from .mi_models import MiRecord, ParsedMiResponse


def parse_mi_responses(responses: list[MiRecord]) -> ParsedMiResponse:
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
            parsed.result = mi_payload(response.get("payload"))
            message = response.get("message")
            parsed.result_class = message if isinstance(message, str) else None
        elif msg_type == "notify":
            message = response.get("message")
            parsed.notify.append(
                {
                    "message": message if isinstance(message, str) else None,
                    "payload": mi_payload(response.get("payload")),
                }
            )

    return parsed


def mi_payload(value: object) -> Mapping[str, object] | None:
    """Return the fields of one MI result or notification record.

    MI spells a record as a list of ``key=value`` fields, so pygdbmi hands back
    a mapping whenever the record carried fields and ``None`` when it carried
    none. Any other shape means the wire format and this parser disagree, which
    is raised here instead of being read as "this record had no fields".
    """

    if value is None or isinstance(value, Mapping):
        return value
    raise TypeError(f"MI record payload must be a mapping or absent, got {type(value).__name__}")


def extract_mi_result_payload(
    command_result: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Return the MI result payload carried by one command result.

    The container is :meth:`ParsedMiResponse.to_dict`, whose ``result`` field is
    the payload mapping or ``None`` when the command reported no fields, so the
    field is read as that type rather than re-narrowed here.
    """

    return cast("Mapping[str, object] | None", command_result.get("result"))
