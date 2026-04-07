"""Human-oriented renderers for CLI tool responses."""

from __future__ import annotations


def _render_mapping_lines(payload: dict[str, object], *, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    lines: list[str] = []

    for key, value in payload.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.extend(_render_mapping_lines(value, indent=indent + 1))
            continue

        lines.append(f"{prefix}{key}: {value}")

    return lines


def render_mapping(payload: dict[str, object]) -> str:
    """Render one response mapping as labeled lines."""

    return "\n".join(_render_mapping_lines(payload))


def render_session_start(payload: dict[str, object]) -> str:
    """Render the key fields from a session-start response."""

    summary = {
        "status": payload.get("status"),
        "session_id": payload.get("session_id"),
        "target_loaded": payload.get("target_loaded"),
        "execution_state": payload.get("execution_state"),
        "message": payload.get("message"),
    }
    return render_mapping(summary)
