class ToolValidationError(Exception):
    """Raised when a tool name or argument payload is invalid."""


class ToolExecutionError(Exception):
    """Raised when a known tool cannot complete its operation."""


class ApprovalRequired(Exception):
    """Internal control flow marker for an action awaiting approval."""
