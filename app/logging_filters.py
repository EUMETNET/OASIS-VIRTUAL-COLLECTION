"""Logging filters for the OASIS Virtual Collection API."""

import logging


class EndpointFilter(logging.Filter):
    """
    Suppress uvicorn access-log entries for specific URL paths.

    Uvicorn logs each request via the ``uvicorn.access`` logger with args:
      (client_addr, method, path_qs, http_version, status_code)

    where ``path_qs`` is the raw path including any query string.  This filter
    strips the query string before matching so that, for example, ``/health``
    silences both ``GET /health`` and ``GET /health?verbose=1``.

    Usage::

        import logging
        logging.getLogger("uvicorn.access").addFilter(
            EndpointFilter(["/health", "/metrics"])
        )
    """

    def __init__(self, paths: list[str]) -> None:
        super().__init__()
        self._paths = set(paths)

    def filter(self, record: logging.LogRecord) -> bool:
        # args[2] is the path (possibly with query string) in uvicorn's access log
        if record.args and len(record.args) >= 3:
            path = str(record.args[2]).split("?")[0]
            if path in self._paths:
                return False
        return True
