# Core Concepts

## 1. 三层心智模型

FastEvents 可以分成三层：

### 运行时层

- [`Bus`](fastevents/bus.py:21)
- [`InMemoryBus`](fastevents/bus.py:100)

这一层关心：

- 生命周期
- admission
- event loop
- 队列与调度入口

### 应用核心层

- [`Dispatcher`](fastevents/dispatcher.py:33)
- 注入解析逻辑，主要位于 [`fastevents/subscribers.py`](fastevents/subscribers.py)

这一层关心：

- 哪些 subscriber 匹配事件
- 如何按 `level` 传播
- 如何把 payload / dependency 变成 handler 输入

### 顶层表达层

- handler
- extension，例如 [`RpcExtension`](fastevents/ext/rpc.py:155)

这一层关心业务逻辑，不直接关心 bus 内部细节。

## 2. 两种事件对象

### [`StandardEvent`](fastevents/events.py:16)

这是标准事件对象，代表跨 app / bus 边界的事件表示。

它包含：

- `id`
- `timestamp`
- `tags`
- `meta`
- `payload`

### [`RuntimeEvent`](fastevents/events.py:101)

这是 handler 执行期的运行时视图。

相比 [`StandardEvent`](fastevents/events.py:16)，它额外提供：

- [`ctx`](fastevents/events.py:109)

也就是运行时能力入口。

## 3. app 边界与 bus 边界

这是这个框架最重要的边界之一。

### app 边界

在 [`FastEvents.publish()`](fastevents/app.py:83) 或 [`EventContext.publish()`](fastevents/events.py:123) 这里，你可以传入更友好的 Python 值。

### bus 边界

进入 [`StandardEvent`](fastevents/events.py:16) 之后，payload / meta 会变成 bus-facing 的最小发送值。

目前这个边界的编码逻辑在：

- [`encode_app_value()`](fastevents/events.py:55)
- [`encode_event_meta()`](fastevents/events.py:86)

## 4. 为什么需要 dispatcher

[`Dispatcher`](fastevents/dispatcher.py:33) 的职责不是 transport，而是语义执行：

- 匹配 subscriber
- 构建 [`RuntimeEventView`](fastevents/events.py:173)
- 创建依赖作用域
- 按 level 执行传播

所以它是“应用语义核心”，不是 bus 的附属工具。
