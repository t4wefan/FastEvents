# FastEvents

**把优秀 Python Web 框架的编程模型，带到通用事件系统里。**

FastEvents 不只是一个 event bus。

它更像一个面向事件应用的编程模型：

- 用声明式 handler 组织业务
- 用结构化注入构造 handler 输入
- 用清晰的 runtime boundary 隔离运行时复杂度
- 用事件组合出更高层协议，比如 RPC
- 给未来的 stream、proxy、生命周期事件和分布式 bus 留出空间

如果你喜欢现代 Python Web 框架那种“handler 很清楚、依赖很自然、边界很明确”的体验，FastEvents 想把这种体验推广到更一般的事件系统里。

---

## 安装

如果你直接从仓库使用：

```bash
uv add https://github.com/t4wefan/FastEvents.git
```

本地开发：

```bash
uv sync
```

当前实现基于 Python `3.12+`，并内置一个内存版 `InMemoryBus`。

---

## 快速开始

先看一个最小的 hello world：

```python
from fastevents import FastEvents, RuntimeEvent

app = FastEvents()

@app.on("hello")
async def hello(event: RuntimeEvent) -> None:
    print("hello world")
```

只看这四行，其实就能理解 FastEvents 最基础的使用方式：

- 导入 `FastEvents`
- 声明一个 `app`
- 用 `@app.on(...)` 注册 handler
- 用一个普通 async 函数表达事件处理逻辑

如果你继续往下看，FastEvents 会在这个最小模型上继续提供：

- `bus` 拥有 runtime boundary
- `app` 负责声明行为
- handler 可以保持很小、很直接
- RPC 不是另一套核心模型，而是建立在事件之上的组合协议

再看一个稍微复杂一点的例子，它会同时展示：

- dependency 注入
- `event.ctx.publish()`
- `pydantic` payload 校验

```python
import asyncio

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RuntimeEvent, dependency

app = FastEvents()
bus = InMemoryBus()


class WorldPayload(BaseModel):
    text: str


@dependency
def say_hello(event: RuntimeEvent) -> bool:
    return event.payload is not None


@app.on("hello")
async def hello(event: RuntimeEvent, ok: bool = say_hello()) -> None:
    if ok:
        await event.ctx.publish(tags="world", payload={"text": "world"})


@app.on("world")
async def world(data: WorldPayload) -> None:
    print(data.text)


async def main() -> None:
    await bus.astart(app)
    try:
        await app.publish(tags="hello", payload={"message": "hello"})
    finally:
        await bus.astop()


asyncio.run(main())
```

这里的意思是：

- `hello` handler 不直接自己判断，而是通过 dependency 注入 `say_hello()` 的结果
- `say_hello()` 依赖当前 `event`，只要 payload 不为空就返回 `True`
- `hello` handler 在条件满足时继续发布一个 `world` 事件
- `world` handler 用 `pydantic` 模型校验 payload，校验通过后打印 `world`

---

## 为什么它不只是另一个事件总线

很多事件库更接近 transport 或 callback registry。

FastEvents 想解决的是另一个问题：

**如何像写 Web 应用一样写事件应用。**

也就是说，重点不是“把消息送出去”，而是：

- 如何清晰地声明谁处理什么事件
- 如何把 payload、context、dependency 自然注入到 handler
- 如何让后续事件、request/reply、fallback 都保持在同一套模型里
- 如何把 runtime 复杂度限制在 bus 层，而不是污染 app 层

它的目标不是把所有能力都塞进核心，而是把核心边界做清楚，让更高层协议自然长出来。

---

## 设计边界

FastEvents 的核心吸引力，不只是“能用”，而是语义克制、边界清楚。

### 1. `publish()` 是 transport boundary，不是完成保证

发布一个事件，表示把它提交给 bus。

`publish()` **不保证**：

- 某个 handler 已经执行
- 某个 handler 一定成功
- 所有处理已经完成
- 分布式网络已经达成全局一致

它只保证：

**事件已经被 bus 接收，并进入 bus 的发送边界。**

这是刻意的设计。这样未来无论底层是本地内存 bus，还是远程 broker / 分布式 bus，`publish()` 的语义都不需要改变。

如果你需要 reply、completion、aggregation、ack 等能力，它们应该建立在事件之上，而不是反过来定义 `publish()`。

### 2. `app` 不是 full host

`FastEvents` 是声明与组合容器。

它不是完整宿主，不负责托管所有资源，也不试图成为一个框架级插件平台。运行时复杂度属于 bus，而不是 app。

### 3. extension 是组合，不是框架托管插件

extension 应该是普通对象，它们基于 `app` 的公开能力组合出更高层功能。

- 它们不应该依赖框架内部细节
- 它们不应该拥有特殊地位
- 它们的资源归属应保持显式

推荐的方向是显式构造：

```python
from fastevents import FastEvents
from fastevents.ext.rpc import RpcExtension

app = FastEvents()
rpc = RpcExtension(app)
```

相比历史上的 `app.ex` 风格，这种方式更清晰，也更利于类型推断。

### 4. runtime complexity 属于 bus

bus 负责：

- 接收发布的事件
- 绑定或创建事件循环
- 管理 runtime 生命周期
- 把 transport 层事件送入应用核心
- 承接未来可能出现的远程传输或分布式交付复杂度

这些复杂度属于 runtime 层，不应该污染应用模型。

---

## 一个简单的心智模型

你可以把 FastEvents 理解成三层：

### Runtime 层：bus

`bus` 是最底层的 runtime 边界。

它决定事件如何被接收、运行时如何启动、loop 归谁管理，以及事件如何进入应用核心。

### 应用核心：dispatcher + injector

应用核心负责把一个事件变成一次明确的应用调用：

- `dispatcher` 选择哪些 subscriber 匹配
- `injector` 根据 payload、context、dependency 构造 handler 输入
- 然后执行实际调用

### 顶层：handlers + extensions

- `handler` 表达业务行为
- `extension` 用公开能力组合出更高层协议，例如 RPC

它们都建立在同一个应用核心之上，而不是各自发明一套模型。

---

## 传播模型

FastEvents 使用基于 `level` 的传播模型。

直观理解：

- 更低 level 先执行
- 同一 level 内并发执行
- `level < 0` 是观察层，不消费事件
- `level >= 0` 是处理层和 fallback 层

更具体地说：

- dispatcher 先找出所有匹配的 subscriber
- subscriber 按 `level` 分组
- 同一组里的 subscriber 并发运行
- 如果某个非负 `level` 中有 subscriber 消费了事件，后续更高 level 就不再继续
- 如果该 level 全部都没有消费，传播继续向上

这里的 `consumed` 是传播语义，不是“业务一定成功”的信号。

一个常见约定是：

- `-1`：audit、metrics、tracing、被动观察者
- `0`：主业务处理器
- `1+`：fallback

---

## Handler 注入

FastEvents 支持结构化参数注入。你声明 handler 签名，框架负责把当前事件上下文组装出来。

常见注入方式：

- `RuntimeEvent`：获取当前事件
- `dict`：获取原始 payload
- `pydantic.BaseModel`：获取校验后的结构化 payload

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
async def raw_payload(payload: dict) -> None:
    ...


@app.on("order.created")
async def typed_payload(event: RuntimeEvent, data: OrderCreated) -> None:
    ...
```

如果 `pydantic` 模型校验失败，当前 handler 会被视为放弃消费，事件仍然可以继续向更高 level 传播。

---

## Dependency 注入

除了 payload 和 event context，FastEvents 也支持轻量函数式 dependency：

```python
from fastevents import EventContext, RuntimeEvent, dependency


@dependency
def order_ctx(event: RuntimeEvent, ctx: EventContext) -> tuple[str, bool]:
    return (event.id, ctx is event.ctx)


@app.on("order.created")
async def handle(info=order_ctx()) -> None:
    print(info)
```

dependency 还可以继续依赖：

- 当前 event
- `ctx`
- payload 注入
- 其他 dependency

解析结果会在当前事件的一次 dispatch 中缓存，因此同一事件上的多个 handler 可以复用同一个 dependency 结果。

---

## `SessionNotConsumed` 与 fallback

如果主 handler 想明确放弃处理，可以抛出 `SessionNotConsumed`。

这样事件会继续向更高 `level` 传播，非常适合表达 fallback：

```python
from fastevents import SessionNotConsumed


@app.on("user.lookup", level=0)
async def primary(data: dict) -> None:
    if data.get("source") == "legacy":
        raise SessionNotConsumed()


@app.on("user.lookup", level=1)
async def fallback(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.publish(tags="user.lookup.fallback", payload={"path": "fallback", **payload})
```

---

## `RuntimeEvent` 与 `ctx`

如果你需要在 handler 内继续和 bus 交互，例如发布后续事件，就通过 `event.ctx` 完成。

最常见的能力是：

- `await event.ctx.publish(...)`

示例：

```python
@app.on("order.created")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.publish(tags="order.validated", payload=payload)
```

这样 handler 不需要手动持有 bus 引用，和 runtime 的交互边界也更集中。

---

## Bus 生命周期

异步环境里：

```python
await bus.astart(app)
try:
    ...
finally:
    await bus.astop()
```

同步环境里：

```python
bus.start(app)
try:
    ...
finally:
    bus.stop()
```

还可以使用 `bus.run(app)` 进入阻塞式 runtime 循环。

在 bus 启动前，调用 `publish()`，或者在 app 还未绑定到运行中 bus 时调用 `app.publish()` / `app.listen()`，都会抛出 `BusNotStartedError`。

---

## Listen

`listen()` 挂在 `app` 上，用来创建临时 stream subscriber：

```python
async with app.listen("notification.sent", level=-1) as stream:
    async for event in stream:
        print(event.payload)
```

这适合在运行时临时观察某类事件，而不必提前把它声明成长期 handler。

---

## RPC

RPC 不是 FastEvents 的核心模型。

它是建立在事件之上的高层协议。

当前 RPC extension 提供：

- `request_stream()`
- `request_one()`
- `request()`
- `rpc_context()`

这些能力让 request/reply 模式可以建立在普通事件之上，而不会反过来污染核心事件语义。

---

## 最小示例

如果你只想看最基础的事件发布：

```python
import asyncio

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus

app = FastEvents()
bus = InMemoryBus()


class Message(BaseModel):
    text: str


@app.on("message")
async def handle_message(payload: Message) -> None:
    print(f"got: {payload.text}")


async def main() -> None:
    await bus.astart(app)
    try:
        await app.publish(tags="message", payload=Message(text="hello"))
    finally:
        await bus.astop()


asyncio.run(main())
```

---

## 更真实的示例

`demo.py` 更接近这个项目当前真正想表达的方向，它组合了：

- dependency injection
- streamed token events
- `ctx.publish()`
- RPC request/reply
- 交互式终端行为

如果你想理解 FastEvents 在真实应用里的“感觉”，建议直接运行它：

```bash
uv run python demo.py
```

---

## 当前状态

FastEvents 正在向一个边界更清晰的“事件应用模型”演化。

如果你正在评估它，最重要的几件事是：

- `publish()` 的保证刻意保持克制
- bus 拥有 runtime 复杂度
- app 负责声明与组合
- extension 是显式组合对象，而不是托管插件
- 更高层协议应该建立在事件之上，而不是塞进核心模型里

---

## 一句话哲学

FastEvents 想把 Python Web 框架里已经被证明有效的应用编程模型，从“特殊事件 HTTP”推广到“通用事件系统”，同时把边界保持得足够清楚，让整个系统仍然可组合、可扩展、可演进。
