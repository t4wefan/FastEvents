## FastEvents

`FastEvents` 是一个轻量、通用的 Python `asyncio` 事件总线，提供清晰的事件模型、简洁的运行时和易于扩展的 bus 抽象。

简单几行代码，就可以在 Python 环境中直接体验事件式编程：

```python
from fastevents import FastEvents, RuntimeEvent

app = FastEvents()

@app.on("hello")
async def hello(event: RuntimeEvent) -> None:
    print("hello world")
```

当前实现要求 Python `3.12+`，并内置一个内存版 bus。

## 安装

```bash
uv add https://github.com/t4wefan/FastEvents.git
```

本地开发可用：

```bash
uv sync
```

## 快速开始

先定义一个 app，注册一个事件处理函数：

```python
import asyncio
from fastevents import FastEvents, InMemoryBus, RuntimeEvent

app = FastEvents()

@app.on("hello")
async def hello(event: RuntimeEvent) -> None:
    print("hello world")
```

然后启动 bus，发送一个事件：

```python
bus = InMemoryBus()

async def main() -> None:
    await bus.astart(app)
    try:
        await bus.publish(tags="hello")
    finally:
        await bus.astop()

asyncio.run(main())
```

这就是最基本的使用方式：

- 用 `FastEvents()` 创建应用
- 用 `@app.on(...)` 注册 handler
- 用 `InMemoryBus()` 启动运行时并发送事件

## 核心模型

最常用的两个对象是：

- `FastEvents`：声明式门面，用来注册 subscriber
- `InMemoryBus`：运行时对象，负责启动 app，并提供 `publish()`

典型初始化方式：

```python
app = FastEvents()
bus = InMemoryBus()
```

通过 `@app.on(...)` 注册 subscriber。运行时的临时监听和单次请求则由 `app.listen()`、`app.request()` 提供。

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

订阅支持一套紧凑的 tag DSL，可以使用多个 tag 实现有逻辑的组合，处理复杂的情况：

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

我们创新地使用了 `level` 机制来控制事件的并行和串行处理。

- `level < 0`：只观察，不消费事件
- `level >= 0`：处理层与 fallback 层

传播规则：

- subscriber 会先按 level 分组
- 更低的 level 先执行
- 同一 level 内的 subscriber 并发执行
- 负 level 永远不会消费事件
- 如果某个非负 level 中任意 subscriber 返回 `consumed=True`，则更高 level 不再运行
- 如果该非负 level 所有 subscriber 都返回 `consumed=False`，则继续向上层传播

看起来很复杂？其实只用知道一层里如果没有 handler 明确拒绝消费或者一个事件不匹配所有 handler 的规则就会传递到下一层。

默认情况下，注册时会使用 `0` 作为 `level`。

## 用 `SessionNotConsumed` 做 fallback

如果你希望当前 handler 明确放弃这次处理，可以抛出 `SessionNotConsumed`。这样事件会继续向更高 `level` 传播，用于实现主处理器失败后的 fallback。

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

## Handler 注入

FastEvents 内置了常用参数的自动注入。你只需要通过类型标注声明参数，框架就会把当前事件或 payload 传给 handler。

通常你会这样写：

 - 用 `RuntimeEvent` 获取当前事件
 - 用 `dict` 获取原始 payload
 - 用 `pydantic.BaseModel` 直接接收校验后的结构化数据

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

如果使用 `pydantic` 模型注入时 payload 因为不符合 model 而校验失败，当前 handler 会被视为放弃消费，事件可以继续交给更高 `level` 的 handler 处理。


## RuntimeEvent 与 `ctx`

在 handler 内，如果需要继续和总线通信，例如发布后续事件或回复一次请求，就通过 `event.ctx` 来完成。

- `await event.ctx.publish(...)`
- `await event.ctx.reply(...)`

`publish()` 用来继续发布新事件：

```python
@app.on("order.created")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.publish(tags="order.validated", payload=payload)
```

如果希望后续 handler 可以对这个事件执行 `reply()`，也可以在发布时显式传入 `reply_tags`：

```python
await bus.publish(tags="user.lookup", payload={"user_id": 7}, reply_tags="reply.user.lookup")
```

`reply()` 用来响应一次 request/reply：

```python
@app.on("user.lookup")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.reply(payload={"user_id": payload["user_id"], "name": "Alice"})
```

这样你就不需要在 handler 里手动查找 bus 依赖，和总线通信的入口也会更集中。

## Bus 生命周期

bus 可以异步地启动：

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

另外还提供 `bus.run(app)` 这种阻塞式运行方式。这意味着 bus 会占用整个主线程，这之后的代码将不会运行。
一般来说，如果你不手动 stop，在脚本退出时也会自动回收。

在启动前调用 `publish()`，或者在 app 尚未绑定运行时 bus 时调用 `listen()`、`request()`，都会抛出 `BusNotStartedError`。

## Publish

```python
await bus.publish(tags="order.created", payload={"order_id": 1})
```

发出一个消息，但是 `publish()` 只保证事件已经被创建并被 bus 接收。


## Listen

`listen()` 挂在 `app` 上，用来创建一个临时的 stream subscriber。

```python
async with app.listen("notification.sent", level=-1) as stream:
    async for event in stream:
        print(event.payload)
```

这样你就能在运行时临时监听某一类事件，而不需要提前把它声明成长期 handler。

## Request

`request()` 挂在 `app` 上，是标准的单次回复接口。它内部会按这个顺序完成几件事：

- 先生成一组随机的 `reply_tags`
- 再利用随机的 tags 注册一个临时 listener，用来接收 reply
- 然后发布请求事件，并把 `reply_tags` 写入事件
- 如果 request 被正确处理，那么应该会收到带有指定 tags 的 reply，等待第一条匹配的 reply 并返回

```python
reply = await app.request(
    tags="user.lookup",
    payload={"user_id": 7},
    timeout=1,
)
```


如果你关心实现细节或设计语义，可以看 `rfc.md` 以及项目源代码。
