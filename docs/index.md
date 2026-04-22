# FastEvents 文档

这套文档按“由浅入深、按模块拆分”的方式组织，目标是让你先会用，再理解边界与设计。

## 阅读顺序

### 1. 入门

- [`docs/getting-started.md`](docs/getting-started.md)

先跑通一个最小示例，建立对 [`FastEvents`](fastevents/app.py:18)、[`InMemoryBus`](fastevents/bus.py:100) 和 [`FastEvents.on()`](fastevents/app.py:25) 的直觉。

### 2. 核心概念

- [`docs/core-concepts.md`](docs/core-concepts.md)

理解这个框架的几个核心对象：

- [`FastEvents`](fastevents/app.py:18)
- [`Bus`](fastevents/bus.py:21)
- [`Dispatcher`](fastevents/dispatcher.py:33)
- [`StandardEvent`](fastevents/events.py:16)
- [`RuntimeEvent`](fastevents/events.py:173)

### 3. 事件与传播

- [`docs/events-and-propagation.md`](docs/events-and-propagation.md)

重点看：

- 事件如何发送
- payload 如何编码
- `level` 如何控制传播
- [`SessionNotConsumed`](fastevents/exceptions.py:17) 如何触发 fallback

### 4. 依赖注入

- [`docs/dependency-injection.md`](docs/dependency-injection.md)

重点看：

- [`dependency()`](fastevents/subscribers.py:63)
- [`EventModel`](fastevents/models.py:13)
- 静态 [`_provider()`](fastevents/subscribers.py:158)
- 哪些类型可以直接注入

### 5. Bus 与运行时

- [`docs/bus-and-runtime.md`](docs/bus-and-runtime.md)

重点看：

- [`Bus.start()`](fastevents/bus.py:132)
- [`Bus.astart()`](fastevents/bus.py:152)
- [`Bus.stop()`](fastevents/bus.py:158)
- [`Bus.publish()`](fastevents/bus.py:205)
- [`InMemoryBus.ingest()`](fastevents/bus.py:244)

### 6. RPC 扩展

- [`docs/rpc-extension.md`](docs/rpc-extension.md)

重点看：

- [`RpcExtension`](fastevents/ext/rpc.py:155)
- [`RpcContext`](fastevents/ext/rpc.py:107)
- [`rpc_context()`](fastevents/ext/rpc.py:150)

## 文档约定

- 面向使用者的入口尽量从 [`fastevents/__init__.py`](fastevents/__init__.py:1) 开始介绍
- 涉及运行时语义时，会明确区分 app 边界与 bus 边界
- 涉及注入时，会明确区分“特权类型”、“快速注入类型”和“provider 类型”
