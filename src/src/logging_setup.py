"""Shared logging configuration and an input/output debug decorator.

One place configures the log format and level so every module logs
consistently. The format includes the **time, level, filename, function name,
line number** and the message:

    2026-10-21 10:39:12,180 DEBUG [agent.py:place_order:210] -> input: {...}

``LOG_LEVEL`` (env var, default ``INFO``) controls verbosity. Set it to
``DEBUG`` to see the per-function input/output logs emitted by ``@log_io`` and
the per-endpoint logs in ``app.py``.

Usage:
    from logging_setup import get_logger, log_io

    LOG = get_logger(__name__)

    @log_io()                 # logs input args + return value at DEBUG
    def my_func(a, b): ...

On AWS Lambda the runtime installs a root handler, so we configure that handler
(or add one locally) rather than calling basicConfig, which Lambda ignores.
"""

import functools
import contextvars
import logging
import os
import uuid

# Correlation ids injected into every log line (see ContextFilter below):
#   request_id          - the Lambda invocation id (context.aws_request_id),
#                         stable for the whole invocation.
#   endpoint_request_id - a per-endpoint-call id, unique to each /ask, /callback,
#                         SQS message, etc.
_request_id = contextvars.ContextVar("request_id", default="-")
_endpoint_request_id = contextvars.ContextVar("endpoint_request_id", default="-")

# filename:function:line makes each line traceable to its exact source;
# req/ep carry the Lambda request id and the per-endpoint request id.
LOG_FORMAT = (
    "%(asctime)s %(levelname)s "
    "[req=%(request_id)s ep=%(endpoint_request_id)s] "
    "[%(filename)s:%(funcName)s:%(lineno)d] %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


class _ContextFilter(logging.Filter):
    """Attach the current request_id / endpoint_request_id to every record."""

    def filter(self, record):
        record.request_id = _request_id.get()
        record.endpoint_request_id = _endpoint_request_id.get()
        return True


_context_filter = _ContextFilter()


# --- correlation-id helpers ------------------------------------------------


def set_request_id(request_id):
    """Set the Lambda invocation id for all logs in this invocation."""
    _request_id.set(request_id or "-")


def set_endpoint_request_id(value=None):
    """Set (or generate) the per-endpoint-call id. Returns the id used."""
    value = value or uuid.uuid4().hex[:12]
    _endpoint_request_id.set(value)
    return value


def reset_ids():
    """Clear both correlation ids (used between SQS records / invocations)."""
    _request_id.set("-")
    _endpoint_request_id.set("-")


def _level():
    return os.environ.get("LOG_LEVEL", "INFO").strip().upper()


def configure_logging():
    """Configure the root logger's format + level once (idempotent).

    Works both locally (adds a StreamHandler) and on Lambda (reuses the
    runtime-provided handler but applies our formatter and level). The
    correlation-id filter is attached to every handler so request_id and
    endpoint_request_id appear on every line.
    """
    global _configured
    root = logging.getLogger()
    level = _level()
    root.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=_DATE_FORMAT)
    if root.handlers:
        # Lambda (and some runtimes) pre-install a handler: restyle it.
        for handler in root.handlers:
            handler.setFormatter(formatter)
            handler.setLevel(level)
            handler.addFilter(_context_filter)
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.setLevel(level)
        handler.addFilter(_context_filter)
        root.addHandler(handler)

    # Also filter at the logger level so records created directly (our manual
    # LogRecord in _emit) still get the ids even if a handler lacks the filter.
    root.addFilter(_context_filter)

    _configured = True


def get_logger(name=None):
    """Return a logger, ensuring logging is configured first."""
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


def _truncate(value, limit=2000):
    """Stringify a value for logs, truncating very long payloads."""
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - never let logging raise
        text = "<unreprable>"
    if len(text) > limit:
        return text[:limit] + f"... <truncated {len(text) - limit} chars>"
    return text


def _emit(log, level, message, func):
    """Emit a log record whose filename/funcName/lineno point at ``func``.

    The decorator logs from inside a wrapper, so by default the record would
    show ``logging_setup.py:_emit``. We build the record manually from
    ``func.__code__`` so each input/output line is attributed to the real
    decorated function (file, name, and its definition line).
    """
    if not log.isEnabledFor(level):
        return
    code = func.__code__
    record = log.makeRecord(
        log.name, level,
        code.co_filename,       # pathname -> %(filename)s
        code.co_firstlineno,    # %(lineno)d (function definition line)
        "%s", (message,),
        None,
        func.__name__,          # %(funcName)s
    )
    # Manually-built records bypass the logger filter chain, so set the
    # correlation ids explicitly here.
    record.request_id = _request_id.get()
    record.endpoint_request_id = _endpoint_request_id.get()
    log.handle(record)


def log_io(logger=None, level=logging.DEBUG):
    """Decorator that logs a function's input args and output at ``level``.

    The entry log records positional + keyword arguments; the exit log records
    the return value (or the exception, re-raised). Uses the decorated
    function's own module logger by default so ``funcName``/``filename`` in the
    format point at the real function.
    """

    def decorator(func):
        log = logger or get_logger(func.__module__)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            _emit(
                log, level,
                "-> input: args=%s kwargs=%s" % (_truncate(args), _truncate(kwargs)),
                func,
            )
            try:
                result = func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - log then re-raise
                _emit(log, level, "!! raised: %s" % _truncate(exc), func)
                raise
            _emit(log, level, "<- output: %s" % _truncate(result), func)
            return result

        return wrapper

    return decorator
