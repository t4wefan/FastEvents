from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from .app import FastEvents
from .events import StandardEvent, format_event_debug, new_event
from .exceptions import BusAlreadyStartedError, BusNotStartedError
from .subscribers import EventStream
from .subscription import SubscriptionInput, TagInput


_runtime_publish_scope: ContextVar[int] = ContextVar("fastevents_runtime_publish_scope", default=0)


class Bus(ABC):
    """Runtime bus contract for a local FastEvents application.

    The bus owns lifecycle, admission into the runtime send boundary, and
    stream-style listening built on top of dispatcher-managed subscribers.
    """

    @abstractmethod
    def run(self, app: FastEvents, *, startup_event: StandardEvent | None = None, shutdown_event: StandardEvent | None = None) -> None:
        """Run the app in a blocking runtime loop.

        If provided, ``startup_event`` is sent after startup is ready.
        If provided, ``shutdown_event`` is sent before drain-based shutdown.
        """
        raise NotImplementedError

    @abstractmethod
    def start(self, app: FastEvents, *, startup_event: StandardEvent | None = None) -> None:
        """Bind the app and enter started state from sync code.

        This method establishes lifecycle state immediately. The actual runtime
        loop resources are initialized once the bus is used from an event loop.
        """
        raise NotImplementedError

    @abstractmethod
    async def astart(self, app: FastEvents, *, startup_event: StandardEvent | None = None) -> None:
        """Async startup that also prepares runtime resources eagerly."""
        raise NotImplementedError

    @abstractmethod
    def stop(self, *, shutdown_event: StandardEvent | None = None) -> None:
        """Synchronously stop the bus and wait for drain to complete."""
        raise NotImplementedError

    @abstractmethod
    async def astop(self, *, shutdown_event: StandardEvent | None = None) -> None:
        """Asynchronously stop the bus and wait for drain to complete."""
        raise NotImplementedError

    @abstractmethod
    async def publish(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, object] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        """Build and accept a new event into the bus send boundary.

        ``publish()`` guarantees admission only. It does not guarantee that
        dispatch has finished or that subscribers have already completed.
        """
        raise NotImplementedError

    @abstractmethod
    def listen(
        self,
        subscription: SubscriptionInput,
        *,
        level: int = 0,
        name: str | None = None,
        maxsize: int = 0,
    ) -> AsyncIterator[EventStream]:
        """Create a temporary stream subscriber for the given subscription."""
        raise NotImplementedError

    @abstractmethod
    async def send(self, event: StandardEvent) -> None:
        """Accept an already-built event into the runtime send boundary."""
        raise NotImplementedError

    @abstractmethod
    def sync_publish(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, object] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        """Synchronously publish one event through the running runtime loop."""
        raise NotImplementedError

    @abstractmethod
    def sync_send(self, event: StandardEvent) -> None:
        """Synchronously send one pre-built event through the running runtime loop."""
        raise NotImplementedError

    @abstractmethod
    async def ingest(self, event: StandardEvent) -> None:
        """Process an already-built event inside the local runtime boundary."""
        raise NotImplementedError

class InMemoryBus(Bus):
    """In-memory bus implementation with queued admission and async dispatch."""

    def __init__(self) -> None:
        self._app: FastEvents | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        self._runtime_thread: threading.Thread | None = None
        self._queue: asyncio.Queue[StandardEvent | None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._pending_startup_event: StandardEvent | None = None
        self._started = False
        self._accepting = False
        self._stopping = False

    def run(self, app: FastEvents, *, startup_event: StandardEvent | None = None, shutdown_event: StandardEvent | None = None) -> None:
        """Run the in-memory bus until stopped.

        The bus starts, optionally emits ``startup_event``, stays alive until
        stopped, then optionally emits ``shutdown_event`` during drain.
        """
        async def runner() -> None:
            await self.astart(app, startup_event=startup_event)
            try:
                while self._started:
                    await asyncio.sleep(0.05)
            finally:
                if self._started:
                    await self.astop(shutdown_event=shutdown_event)

        asyncio.run(runner())

    def start(self, app: FastEvents, *, startup_event: StandardEvent | None = None) -> None:
        """Enter started state and bind the dispatcher runtime publisher.

        Sync startup owns a dedicated background event loop thread.
        """
        self._bind_started_state(app, startup_event=startup_event)
        ready = threading.Event()
        startup_error: list[BaseException] = []

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._loop_thread_id = threading.get_ident()

            async def init_runtime() -> None:
                try:
                    await self._ensure_runtime_ready()
                    await self._flush_pending_startup_event()
                except BaseException as exc:  # pragma: no cover - catastrophic startup path
                    startup_error.append(exc)
                finally:
                    ready.set()

            loop.create_task(init_runtime())
            try:
                loop.run_forever()
            finally:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

        self._runtime_thread = threading.Thread(target=runner, name="FastEventsInMemoryBus", daemon=True)
        self._runtime_thread.start()
        ready.wait()
        if startup_error:
            self._runtime_thread = None
            self._started = False
            self._accepting = False
            self._stopping = False
            self._loop = None
            self._loop_thread_id = None
            raise startup_error[0]

    async def astart(self, app: FastEvents, *, startup_event: StandardEvent | None = None) -> None:
        """Async startup that prepares queue/worker and flushes startup event."""
        self._bind_started_state(app, startup_event=startup_event)
        await self._ensure_runtime_ready()
        await self._flush_pending_startup_event()

    def stop(self, *, shutdown_event: StandardEvent | None = None) -> None:
        """Block until queued events and in-flight dispatch tasks are drained."""
        if not self._started:
            return

        loop = self._loop
        if loop is not None and loop.is_running():
            if threading.get_ident() == self._loop_thread_id:
                raise RuntimeError("bus.stop() cannot block from the event loop thread; call it from sync code or asyncio.to_thread")
            future = asyncio.run_coroutine_threadsafe(self._stop_async(shutdown_event=shutdown_event), loop)
            future.result()
            if self._runtime_thread is not None:
                loop.call_soon_threadsafe(loop.stop)
                self._runtime_thread.join()
                self._runtime_thread = None
            return

        asyncio.run(self._stop_async(shutdown_event=shutdown_event))

    async def astop(self, *, shutdown_event: StandardEvent | None = None) -> None:
        """Async variant of ``stop()`` with the same drain semantics."""
        await self._stop_async(shutdown_event=shutdown_event)

    async def _stop_async(self, *, shutdown_event: StandardEvent | None = None) -> None:
        """Stop accepting external events, optionally emit shutdown, then drain."""
        if not self._started:
            return
        await self._ensure_runtime_ready()
        self._stopping = True
        if shutdown_event is not None:
            await self.send(shutdown_event)
        self._accepting = False
        queue = self._queue
        worker = self._worker_task
        if queue is not None and worker is not None:
            await queue.join()
            pending = list(self._tasks)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await queue.put(None)
            await worker
        self._tasks.clear()
        self._queue = None
        self._worker_task = None
        self._started = False
        self._stopping = False
        self._loop = None
        self._loop_thread_id = None
        if self._app is not None:
            self._app._bind_runtime_bus(None)  # type: ignore[attr-defined]

    def sync_publish(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, object] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        loop = self._require_running_loop()
        future = asyncio.run_coroutine_threadsafe(
            self.publish(tags=tags, payload=payload, meta=meta, id=id, timestamp=timestamp),
            loop,
        )
        return future.result()

    def sync_send(self, event: StandardEvent) -> None:
        loop = self._require_running_loop()
        future = asyncio.run_coroutine_threadsafe(self.send(event), loop)
        future.result()

    async def publish(
        self,
        *,
        tags: TagInput,
        payload=None,
        meta: dict[str, object] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        """Create a new event and enqueue it for runtime admission."""
        await self._ensure_runtime_ready()
        self._ensure_publish_allowed()
        event = new_event(tags=tags, payload=payload, meta=meta, id=id, timestamp=timestamp)
        if self._app is not None and self._app.debug:
            self._app._debug_log(f"outgoing {format_event_debug(event)}")  # type: ignore[attr-defined]
        await self.send(event)
        return event

    @asynccontextmanager
    async def listen(
        self,
        subscription: SubscriptionInput,
        *,
        level: int = 0,
        name: str | None = None,
        maxsize: int = 0,
    ) -> AsyncIterator[EventStream]:
        """Register a temporary stream subscriber and yield its event stream."""
        app = self._require_app()
        async with app.listen(subscription, level=level, name=name, maxsize=maxsize) as stream:
            yield stream

    async def send(self, event: StandardEvent) -> None:
        """Enqueue an already-built event into the in-memory admission queue."""
        await self._ensure_runtime_ready()
        self._ensure_send_allowed()
        queue = self._require_queue()
        await queue.put(event)

    async def ingest(self, event: StandardEvent) -> None:
        """Dispatch one event through the local dispatcher.

        During dispatch, derived ``ctx.publish()`` calls are marked as runtime
        publishes so they can still be admitted while the bus is draining.
        """
        self._ensure_runtime_bound()
        app = self._require_app()
        if app.debug:
            app._debug_log(f"incoming {format_event_debug(event)}")  # type: ignore[attr-defined]
        token = _runtime_publish_scope.set(_runtime_publish_scope.get() + 1)
        try:
            await app.dispatcher.dispatch(event)
        finally:
            _runtime_publish_scope.reset(token)

    async def _ensure_runtime_ready(self) -> None:
        self._ensure_runtime_bound()
        loop = asyncio.get_running_loop()

        if self._loop is None:
            self._loop = loop
            self._loop_thread_id = threading.get_ident()
        elif self._loop is not loop:
            raise RuntimeError("bus runtime is bound to a different event loop")

        if self._queue is None:
            self._queue = asyncio.Queue()

        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._dispatch_worker())

        await self._flush_pending_startup_event()

    async def _dispatch_worker(self) -> None:
        queue = self._require_queue()
        while True:
            event = await queue.get()
            try:
                if event is None:
                    return
                task = asyncio.create_task(self.ingest(event))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            finally:
                queue.task_done()

    async def _flush_pending_startup_event(self) -> None:
        if self._pending_startup_event is None:
            return
        event = self._pending_startup_event
        self._pending_startup_event = None
        await self.send(event)

    def _bind_started_state(self, app: FastEvents, *, startup_event: StandardEvent | None) -> None:
        if self._started:
            raise BusAlreadyStartedError("bus is already started")
        self._app = app
        self._started = True
        self._accepting = True
        self._stopping = False
        self._loop = None
        self._loop_thread_id = None
        self._queue = None
        self._worker_task = None
        self._tasks.clear()
        self._pending_startup_event = startup_event
        self._app.dispatcher.bind_runtime_publisher(self)
        self._app._bind_runtime_bus(self)  # type: ignore[attr-defined]

    def _ensure_started(self) -> None:
        if not self._started or not self._accepting or self._app is None:
            raise BusNotStartedError("bus has not been started")

    def _ensure_publish_allowed(self) -> None:
        if not self._started or self._app is None:
            raise BusNotStartedError("bus has not been started")
        if self._accepting:
            return
        if self._stopping and _runtime_publish_scope.get() > 0:
            return
        raise BusNotStartedError("bus has not been started")

    def _ensure_send_allowed(self) -> None:
        self._ensure_publish_allowed()

    def _ensure_runtime_bound(self) -> None:
        if not self._started or self._app is None:
            raise BusNotStartedError("bus has not been started")

    def _require_app(self) -> FastEvents:
        if self._app is None:
            raise BusNotStartedError("bus has not been started")
        return self._app

    def _require_running_loop(self) -> asyncio.AbstractEventLoop:
        loop = self._loop
        if loop is None or not loop.is_running() or not self._started:
            raise BusNotStartedError("bus runtime loop is not available")
        return loop

    def _require_queue(self) -> asyncio.Queue[StandardEvent | None]:
        if self._queue is None:
            raise BusNotStartedError("bus runtime queue is not available")
        return self._queue
