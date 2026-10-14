from __future__ import annotations
import re
_ACTION_PHRASES: dict[str, tuple[str, ...]] = {
    "retry_sync": (
        "retry",
        "resync",
        "repeat synchronization",
    ),
    "create_ticket": (
        "create a ticket",
        "create ticket",
        "open a ticket",
        "open ticket",
        "create an incident",
        "create incident",
        "open an incident",
        "open incident",
    ),
}
_NEGATION_PATTERN = re.compile(r"\b(?:do not|don't|never|not|without|no)\b")
def is_explicit_action_request(request: str, tool_name: str) -> bool:
    for phrase in _ACTION_PHRASES.get(tool_name, ()):
        for match in re.finditer(re.escape(phrase), request.lower()):
            prefix = request.lower()[max(0, match.start() - 40) : match.start()]
            if _NEGATION_PATTERN.search(prefix) and not re.search(
                r"[.!?;]\s*[^.!?;]*$",
                prefix,
            ):
                continue
            return True
    return False
