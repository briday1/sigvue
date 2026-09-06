"""Socket-free transport for the existing web application in a Python worker.

Jobs use the worker's asyncio loop, not threads. Yield to that loop between
messages (``await asyncio.sleep(0)``), or await ``runtime.drain()`` to finish
queued jobs. A synchronous export or batch occupies the worker until its
callback returns: it is not parallel and cannot be cancelled mid-callback.
Without a running loop, jobs execute immediately.
"""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Executor, Future
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sigvue.web.application import _make_handler, create_app


class _EventLoopExecutor(Executor):
    def __init__(self) -> None:
        self._pending: set[Future] = set()
        self._shutdown = False

    def submit(self, fn, /, *args, **kwargs) -> Future:
        if self._shutdown:
            raise RuntimeError("cannot schedule new futures after shutdown")
        future = Future()
        self._pending.add(future)

        def run() -> None:
            try:
                if not future.set_running_or_notify_cancel():
                    return
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            finally:
                self._pending.discard(future)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            run()
        else:
            loop.call_soon(run)
        return future

    async def drain(self) -> None:
        while self._pending:
            await asyncio.sleep(0)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """Stop submissions; use drain() to await callbacks without blocking."""
        self._shutdown = True
        if cancel_futures:
            for future in tuple(self._pending):
                future.cancel()


class BrowserRuntime:
    """Run profile-backed HTTP routes without opening sockets.

    ``request(method, path, body="")`` returns a plain dictionary containing
    ``status`` (integer), ``headers`` (dictionary), and ``body`` (base64 ASCII).
    Request bodies are UTF-8 strings; response bytes are never text-decoded.
    """

    def __init__(self, config_path: str | Path) -> None:
        self.app = create_app(config_path=config_path, reload_workspaces=False)
        self._executor = _EventLoopExecutor()
        # ThreadPoolExecutor creates threads only on submit; these are unused.
        self.app._export_executor.shutdown(wait=False)
        self.app._batch_executor.shutdown(wait=False)
        self.app._export_executor = self._executor
        self.app._batch_executor = self._executor

        class MemoryHandler(_make_handler(self.app)):
            def __init__(self, method: str, path: str, body: str) -> None:
                data = body.encode("utf-8")
                target = quote(path, safe="/%?=&:+,;@!$'()*[]#-._~")
                head = (
                    f"{method} {target} HTTP/1.1\r\n"
                    f"Content-Length: {len(data)}\r\n"
                    "Content-Type: application/json\r\n\r\n"
                )
                self.rfile = BytesIO(head.encode("utf-8") + data)
                self.wfile = BytesIO()
                self.response_status = 500
                self.response_headers: dict[str, str] = {}

            def send_response(self, code: int, message: str | None = None) -> None:
                self.response_status = int(code)

            def send_header(self, keyword: str, value: str) -> None:
                self.response_headers[keyword] = value

            def end_headers(self) -> None:
                pass

        self._handler = MemoryHandler

    def request(self, method: str, path: str, body: str = "") -> dict[str, Any]:
        """Dispatch one request through the same parser and routes as HTTP."""
        if any(character in method + path for character in "\r\n\0"):
            raise ValueError("Request method and path cannot contain control characters")
        handler = self._handler(method, path, body)
        handler.handle_one_request()
        return {
            "status": handler.response_status,
            "headers": handler.response_headers,
            "body": base64.b64encode(handler.wfile.getvalue()).decode("ascii"),
        }

    async def drain(self) -> None:
        """Finish queued jobs cooperatively; failures remain in job statuses."""
        await self._executor.drain()
