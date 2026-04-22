# Getting Started

## 1. 最小可运行示例

先看最小代码：

```python
import asyncio

from fastevents import FastEvents, InMemoryBus, RuntimeEvent

app = FastEvents()
bus = InMemoryBus()


@app.on("hello")
async def hello(event: RuntimeEvent) -> None:
    print(event.tags, event.payload)


async def main() -> None:
    await bus.astart(app)
    try:
        await app.publish(tags="hello", payload={"message": "world"})
    finally:
        await bus.astop()


asyncio.run(main())
```

这个例子里有三件事：

1. 用 [`FastEvents`](fastevents/app.py:18) 声明应用
2. 用 [`InMemoryBus`](fastevents/bus.py:100) 提供运行时
3. 用 [`@app.on(...)`](fastevents/app.py:25) 注册 handler

## 2. 你最先要理解的对象

### [`FastEvents`](fastevents/app.py:18)

它负责：

- 注册 handler
- 暴露 [`publish()`](fastevents/app.py:83)
- 暴露 [`listen()`](fastevents/app.py:51)
- 挂接 [`Dispatcher`](fastevents/dispatcher.py:33)

它不是 transport，不负责自己跑事件循环。

### [`InMemoryBus`](fastevents/bus.py:100)

它负责：

- 绑定 app
- 创建运行时队列
- 把事件送进 dispatcher
- 管理生命周期

### [`RuntimeEvent`](fastevents/events.py:173)

handler 看到的不是裸 payload，而是运行时事件视图。你通常会从它拿到：

- `id`
- `tags`
- `payload`
- `meta`
- `ctx`

## 3. 启动与停止

异步环境优先使用：

- [`bus.astart(app)`](fastevents/bus.py:152)
- [`bus.astop()`](fastevents/bus.py:173)

同步环境可以使用：

- [`bus.start(app)`](fastevents/bus.py:132)
- [`bus.stop()`](fastevents/bus.py:158)

## 4. 发送事件

通常推荐通过 [`app.publish()`](fastevents/app.py:83) 发送。

原因是：

- 它是 app 边界上的公共发送入口
- 更符合扩展和上层代码的使用方式
- 运行时 bus 细节不会泄露到业务代码里

## 5. 下一步读什么

跑通这个例子后，建议继续阅读：

- [`docs/core-concepts.md`](docs/core-concepts.md)
- [`docs/events-and-propagation.md`](docs/events-and-propagation.md)
