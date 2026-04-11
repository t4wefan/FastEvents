from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any, Generic, TypeVar, overload
from uuid import uuid4

from pydantic import BaseModel

from ..events import EventContext, RuntimeEvent, StandardEvent
from ..subscribers import dependency
from ..subscription import TagInput, normalize_tags

if TYPE_CHECKING:
    from ..app import FastEvents


RPC_REPLY_TAGS_KEY = "ex.rpc.reply_tags"
RPC_CORRELATION_ID_KEY = "ex.rpc.id"

ReplyModelT = TypeVar("ReplyModelT", bound=BaseModel)

class RpcRequestTimeoutError(Exception):
    """Raised when an rpc request does not receive a reply in time."""


class RpcReplyNotAvailableError(Exception):
    """Raised when an rpc reply target is unavailable on the current event."""


def _event_to_standard(event: RuntimeEvent) -> StandardEvent:
    return StandardEvent(
        id=event.id,
        timestamp=event.timestamp,
        tags=event.tags,
        meta=event.meta,
        payload=event.payload,
    )


def _validate_model(model: type[ReplyModelT] | None, event: StandardEvent) -> StandardEvent | ReplyModelT:
    if model is None:
        return event
    return model.model_validate(event.payload)


class RpcReplyStream(AsyncIterator[StandardEvent | ReplyModelT], Generic[ReplyModelT]):
    """Async iterator over replies emitted for one rpc request."""

    def __init__(
        self,
        *,
        stream,
        stack: AsyncExitStack,
        timeout: float | None,
        max_size: int | None,
        model: type[ReplyModelT] | None,
    ) -> None:
        self._stream = stream
        self._stack = stack
        self._timeout = timeout
        self._max_size = max_size
        self._model = model
        self._started_at = monotonic()
        self._count = 0
        self._closed = False

    def __aiter__(self) -> RpcReplyStream[ReplyModelT]:
        return self

    async def __anext__(self) -> StandardEvent | ReplyModelT:
        if self._closed:
            raise StopAsyncIteration
        if self._max_size is not None and self._count >= self._max_size:
            await self.close()
            raise StopAsyncIteration

        runtime_event = await self._recv()
        self._count += 1
        result = _validate_model(self._model, _event_to_standard(runtime_event))
        if self._max_size is not None and self._count >= self._max_size:
            await self.close()
        return result

    async def _recv(self) -> RuntimeEvent:
        if self._timeout is None:
            return await self._stream.get()

        remaining = self._timeout - (monotonic() - self._started_at)
        if remaining <= 0:
            raise RpcRequestTimeoutError("rpc request timed out")
        try:
            return await asyncio.wait_for(self._stream.get(), timeout=remaining)
        except TimeoutError as exc:
            raise RpcRequestTimeoutError("rpc request timed out") from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._stack.aclose()


@dataclass(slots=True)
class RpcContext:
    event: RuntimeEvent
    ctx: EventContext

    async def publish(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        return await self.ctx.publish(tags=tags, payload=payload, meta=meta, id=id, timestamp=timestamp)

    async def reply(
        self,
        payload: Any = None,
        *,
        tags: TagInput | None = None,
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        target_tags = tags or self.event.meta.get(RPC_REPLY_TAGS_KEY)
        if target_tags is None:
            raise RpcReplyNotAvailableError("rpc reply tags are not available on the current event")

        reply_meta = dict(meta or {})
        correlation_id = self.event.meta.get(RPC_CORRELATION_ID_KEY)
        if correlation_id is not None:
            reply_meta[RPC_CORRELATION_ID_KEY] = correlation_id

        return await self.ctx.publish(
            tags=target_tags,
            payload=payload,
            meta=reply_meta,
            id=id,
            timestamp=timestamp,
        )


@dependency
def rpc_context(event: RuntimeEvent, ctx: EventContext) -> RpcContext:
    return RpcContext(event=event, ctx=ctx)


class RpcExtension:
    def __init__(self, app: FastEvents | None = None) -> None:
        self._app: FastEvents | None = app

    def bind_app(self, app: FastEvents) -> None:
        self._app = app

    @overload
    async def request_stream(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_size: int | None = None,
        model: type[ReplyModelT],
    ) -> RpcReplyStream[ReplyModelT]:
        ...

    @overload
    async def request_stream(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_size: int | None = None,
        model: None = None,
    ) -> RpcReplyStream[Any]:
        ...

    async def request_stream(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_size: int | None = None,
        model: type[ReplyModelT] | None = None,
    ) -> RpcReplyStream[ReplyModelT]:
        app = self._require_app()
        correlation_id = uuid4().hex
        reply_tags = normalize_tags(("ex.rpc.reply", correlation_id))
        request_meta = dict(meta or {})
        request_meta[RPC_REPLY_TAGS_KEY] = reply_tags
        request_meta[RPC_CORRELATION_ID_KEY] = correlation_id

        stack = AsyncExitStack()
        stream = await stack.enter_async_context(app.listen(reply_tags, level=-1, maxsize=max_size or 0))
        try:
            await app.publish(tags=tags, payload=payload, meta=request_meta)
        except Exception:
            await stack.aclose()
            raise

        return RpcReplyStream(
            stream=stream,
            stack=stack,
            timeout=timeout,
            max_size=max_size,
            model=model,
        )

    @overload
    async def request_one(
        self,
        tags: TagInput,
        model: type[ReplyModelT],
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ReplyModelT:
        ...

    @overload
    async def request_one(
        self,
        tags: TagInput,
        model: None = None,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> StandardEvent:
        ...

    async def request_one(
        self,
        tags: TagInput,
        model: type[ReplyModelT] | None = None,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> StandardEvent | ReplyModelT:
        stream = await self.request_stream(tags=tags, payload=payload, meta=meta, timeout=timeout, max_size=1, model=model)
        try:
            return await stream.__anext__()
        finally:
            await stream.close()

    @overload
    async def request(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_size: int | None = None,
        model: type[ReplyModelT],
    ) -> list[ReplyModelT]:
        ...

    @overload
    async def request(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_size: int | None = None,
        model: None = None,
    ) -> list[StandardEvent]:
        ...

    async def request(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_size: int | None = None,
        model: type[ReplyModelT] | None = None,
    ) -> list[Any]:
        stream = await self.request_stream(
            tags=tags,
            payload=payload,
            meta=meta,
            timeout=timeout,
            max_size=max_size,
            model=model,
        )
        replies: list[StandardEvent | ReplyModelT] = []
        try:
            while True:
                try:
                    replies.append(await stream.__anext__())
                except StopAsyncIteration:
                    return replies
                except RpcRequestTimeoutError:
                    return replies
        finally:
            await stream.close()

    def _require_app(self) -> FastEvents:
        if self._app is None:
            raise RuntimeError("rpc extension is not bound to an app")
        return self._app
