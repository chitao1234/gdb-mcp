"""Shared constants for session service behavior."""

# Timeout constants (in seconds)
DEFAULT_TIMEOUT_SEC = 30
FILE_LOAD_TIMEOUT_SEC = 300  # 5 minutes for loading core/executable files
INTERRUPT_RESPONSE_TIMEOUT_SEC = 2
POLL_TIMEOUT_SEC = 0.1
INIT_COMMAND_DELAY_SEC = 0.5

# Other constants
INITIAL_COMMAND_TOKEN = 1000
DEFAULT_MAX_BACKTRACE_FRAMES = 100
