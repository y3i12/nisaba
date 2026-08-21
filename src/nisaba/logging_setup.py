"""
Centralized logging setup for nisaba.

Routes ALL Python logging (nisaba's own + third-party libraries) to
.nisaba/logs/proxy.log, and optionally redirects the parent process's
OS-level stderr fd to the same file so direct stderr writes from any
library also get captured — while preserving a saved dup of the real
stderr so subprocesses (the claude CLI) still print to the user's
terminal.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional

LOG_PATH = Path(".nisaba/logs/proxy.log")

_saved_real_stderr_fd: Optional[int] = None
_saved_real_stdout_fd: Optional[int] = None
_configured = False


class _SilenceFilter(logging.Filter):
    """Blocks all records — attach to a logger to silence it regardless of
    what handlers or levels get configured later (e.g. by uvicorn's own
    Config.configure_logging that runs after us)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return False


def setup_logging(level: int = logging.INFO) -> None:
    """Route Python logging to proxy.log; quiet noisy third-party loggers."""
    global _configured
    if _configured:
        return
    _configured = True

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH,
        maxBytes=1 * 1024 * 1024,
        backupCount=3,
    )
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    file_handler.setLevel(logging.DEBUG)

    # Reset the root logger: our file is the only sink.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(file_handler)
    root.setLevel(level)

    # nisaba.* → DEBUG, everything routes through root's file handler.
    nisaba_logger = logging.getLogger("nisaba")
    for h in list(nisaba_logger.handlers):
        nisaba_logger.removeHandler(h)
    nisaba_logger.setLevel(logging.DEBUG)
    nisaba_logger.propagate = True

    # Quiet the noisy third parties. Strip any StreamHandlers they've
    # already attached (uvicorn's default config does this) so they can
    # only reach the file via propagation.
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "starlette",
        "httpx",
        "httpcore",
        "mcp",
        "mcp.server",
        "asyncio",
    ):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                lg.removeHandler(h)
        lg.setLevel(logging.WARNING)
        lg.propagate = True

    # uvicorn.access is a MODULE-GLOBAL logger shared across every uvicorn
    # Server instance in-process. FastMCP starts its own uvicorn with the
    # default LOGGING_CONFIG, which attaches a StreamHandler(sys.stdout) to
    # this logger — silently giving our reverse-proxy an access handler we
    # never asked for. A filter that always returns False blocks records at
    # the logger level, before they reach any handler, regardless of what
    # uvicorn's later configure_logging() does with levels or handlers.
    logging.getLogger("uvicorn.access").addFilter(_SilenceFilter())


def capture_std_streams_to_log() -> tuple[int, int]:
    """
    Redirect this process's fd 1 (stdout) and fd 2 (stderr) to proxy.log;
    return a tuple of (saved_stdout_fd, saved_stderr_fd) so callers can
    hand them to a subprocess that should still reach the terminal.

    Idempotent: on repeat calls returns the previously-saved fds.
    """
    global _saved_real_stdout_fd, _saved_real_stderr_fd
    if _saved_real_stdout_fd is not None and _saved_real_stderr_fd is not None:
        return _saved_real_stdout_fd, _saved_real_stderr_fd

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    _saved_real_stdout_fd = os.dup(1)
    _saved_real_stderr_fd = os.dup(2)

    log_fd = os.open(
        str(LOG_PATH),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o644,
    )
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)

    # Rebind sys.stdout / sys.stderr line-buffered so incremental writes flush.
    sys.stdout = os.fdopen(1, 'w', buffering=1)
    sys.stderr = os.fdopen(2, 'w', buffering=1)

    return _saved_real_stdout_fd, _saved_real_stderr_fd


def get_real_stderr_fd() -> int:
    """Return the saved terminal-stderr fd, or fd 2 if capture wasn't called."""
    if _saved_real_stderr_fd is None:
        return 2
    return _saved_real_stderr_fd


def get_real_stdout_fd() -> int:
    """Return the saved terminal-stdout fd, or fd 1 if capture wasn't called."""
    if _saved_real_stdout_fd is None:
        return 1
    return _saved_real_stdout_fd
