## FastEvents

`FastEvents` 是一个轻量级的 `asyncio` 事件框架，适合那些已经超出普通 pub/sub 能力、但又不想上完整工作流引擎或消息中间件的应用。

它聚焦在一组比较小而明确的概念上：

- 基于 `level` 的分层传播
- 显式的一线处理与 fallback 流程
- 通过 `listen()` 建立临时事件流
- 用正式的 request/reply 代替“收集 handler 返回值”
- 通过 `event.ctx` 暴露最小运行时能力

当前实现要求 Python `3.12+`，并内置一个内存版 bus。

## 安装

```bash
uv add fastevents
```

本地开发可用：

```bash
uv sync
```

## 快速开始

```python
from __future__ import annotations

import asyncio

from fastevents import FastEvents, InMemoryBus, RuntimeEvent


app = FastEvents()
bus = InMemoryBus()


@app.on("user.lookup")
async def lookup(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.reply(payload={"user_id": payload["user_id"], "name": "Alice"})


async def main() -> None:
    await bus.astart(app)
    try:
        reply = await bus.request(tags="user.lookup", payload={"user_id": 7}, timeout=1)
        print(reply.payload)
    finally:
        await bus.astop()


asyncio.run(main())
```

## 核心模型

运行时主要有两个对象：

- `FastEvents`：声明式门面，用来注册 subscriber
- `InMemoryBus`：运行时对象，负责启动 app，并提供 `publish()`、`listen()`、`request()`

典型初始化方式：

```python
app = FastEvents()
bus = InMemoryBus()
```

通过 `@app.on(...)` 注册 subscriber，然后启动 bus。

## 事件模型

一个标准事件包含：

- `id`
- `timestamp`
- `tags`
- `meta`
- `payload`

`tags` 会被统一规范化为小写、去重并排序。允许的字符包括字母、数字、`_` 和 `.`。

例子：

- `"order.created"`
- `("order.submitted", "vip")`
- `["payment.failed", "high_value"]`

## Subscription DSL

订阅支持一套紧凑的 tag DSL：

- `"order.created"`：匹配单个 tag
- `("order.submitted", "vip")`：tuple 内所有模式都必须匹配
- `["ops.alert", ("payment.failed", "high_value")]`：list 表示 OR
- `("order.submitted", "-legacy")`：要求命中 `order.submitted`，同时排除 `legacy`
- `"order.*"`：通过 `fnmatch` 做通配匹配

示例：

```python
@app.on("order.created")
async def handle_created(event: RuntimeEvent) -> None:
    ...


@app.on(("order.submitted", "vip"))
async def handle_vip(event: RuntimeEvent) -> None:
    ...


@app.on(["ops.alert", ("payment.failed", "high_value")])
async def handle_ops(event: RuntimeEvent) -> None:
    ...
```

## Level 与传播规则

`level` 是最核心的传播控制机制。

- `level < 0`：只观察，不消费事件
- `level >= 0`：处理层与 fallback 层

传播规则：

- subscriber 会先按 level 分组
- 更低的 level 先执行
- 同一 level 内的 subscriber 并发执行
- 负 level 永远不会消费事件
- 如果某个非负 level 中任意 subscriber 返回 `consumed=True`，则更高 level 不再运行
- 如果该非负 level 所有 subscriber 都返回 `consumed=False`，则继续向上层传播

常见约定：

- `-1`：审计、追踪、指标、被动观察
- `0`：主业务处理器
- `1+`：fallback 处理器

## Handler 注入

当前 v0 的注入模型是刻意收窄的。

支持的参数形式：

- 一个事件参数，标注为 `RuntimeEvent` 或 `StandardEvent`
- 一个 payload 参数，标注为 `dict`
- 一个 payload 参数，标注为 `pydantic.BaseModel` 子类

示例：

```python
from pydantic import BaseModel
from fastevents import RuntimeEvent


class OrderCreated(BaseModel):
    order_id: int


@app.on("order.created")
async def event_only(event: RuntimeEvent) -> None:
    ...


@app.on("order.created")
async def dict_payload(payload: dict) -> None:
    ...


@app.on("order.created")
async def typed_payload(event: RuntimeEvent, data: OrderCreated) -> None:
    ...
```

如果 payload 结构不匹配，某些场景下可以自动让事件继续向更高 level 的 handler fallback。

## 用 `SessionNotConsumed` 做 fallback

对于非负 level 的 handler subscriber，抛出 `SessionNotConsumed` 表示：

- 当前 subscriber 明确放弃 claim 这次事件
- 更高 level 仍然可以继续处理

示例：

```python
from fastevents import SessionNotConsumed


@app.on("user.lookup", level=0)
async def primary(data: dict) -> None:
    if data.get("source") == "legacy":
        raise SessionNotConsumed()


@app.on("user.lookup", level=1)
async def fallback(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.reply(payload={"path": "fallback", **payload})
```

## RuntimeEvent 与 `ctx`

subscriber 在处理时拿到的是运行时事件视图。运行时能力都收敛在 `event.ctx` 上。

当前暴露的方法有：

- `await event.ctx.publish(...)`
- `await event.ctx.reply(...)`

使用 `publish()` 推进流程：

```python
@app.on("order.created")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.publish(tags="order.validated", payload=payload)
```

使用 `reply()` 响应 request/reply：

```python
@app.on("user.lookup")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.reply(payload={"user_id": payload["user_id"], "name": "Alice"})
```

## Bus 生命周期

推荐显式启动和关闭 bus：

```python
await bus.astart(app)
try:
    ...
finally:
    await bus.astop()
```

同步代码中也可以这样用：

```python
bus.start(app)
try:
    ...
finally:
    bus.stop()
```

另外还提供 `bus.run(app)` 这种阻塞式运行方式。

在启动前调用 `publish()`、`listen()`、`request()` 会抛出 `BusNotStartedError`。

## Publish

```python
await bus.publish(tags="order.created", payload={"order_id": 1})
```

这里有一个重要语义边界：`publish()` 只保证事件已经被创建并被 bus 接收到 send boundary。

它不保证：

- dispatch 已经完成
- subscriber 已经成功执行
- reply 已经返回

这个边界是刻意设计的，并且和当前 RFC 一致。

## Listen

`listen()` 会创建一个临时 stream subscriber，并返回一个 async context manager。

```python
async with bus.listen("notification.sent", level=-1) as stream:
    async for event in stream:
        print(event.payload)
```

典型用途：

- 临时观察工具
- UI 事件流
- 测试与 demo
- request/reply 内部机制

## Request / Reply

`request()` 是标准的单次回复接口。

```python
reply = await bus.request(
    tags="user.lookup",
    payload={"user_id": 7},
    timeout=1,
)
```

内部流程：

1. 创建临时 reply subscriber
2. 注册到 dispatcher
3. 发布 request event
4. 等待第一条匹配 reply
5. 无论成功失败都清理临时 subscriber

保留的 metadata 字段：

- `reply_tags`
- `correlation_id`

当上下文中存在这些字段时，`event.ctx.reply()` 会自动使用它们。

如果超时未收到 reply，会抛出 `RequestTimeoutError`。

## 示例

- `python main.py`：最小 smoke 风格示例
- `python demo.py`：分层订单流程 demo
- `python ai_api_demo.py`：启动 FastAPI demo 服务

如果你关心实现细节或设计语义，可以再看 `rfc.md`。
