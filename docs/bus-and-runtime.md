# Bus and Runtime

## 1. 为什么需要 bus

[`FastEvents`](fastevents/app.py:18) 只负责声明与组合。

真正的运行时能力由 [`Bus`](fastevents/bus.py:21) 提供。

## 2. 当前实现

当前唯一实现是 [`InMemoryBus`](fastevents/bus.py:100)。

它提供：

- 启动
- 停止
- admission queue
- worker task
- dispatch 入口

## 3. 生命周期 API

### 异步 API

- [`astart()`](fastevents/bus.py:152)
- [`astop()`](fastevents/bus.py:173)

### 同步 API

- [`start()`](fastevents/bus.py:132)
- [`stop()`](fastevents/bus.py:158)

### 阻塞式运行

- [`run()`](fastevents/bus.py:115)

## 4. 事件如何进入应用核心

核心入口是 [`InMemoryBus.ingest()`](fastevents/bus.py:244)。

它做的事：

1. 检查 runtime 已绑定
2. 在 debug 模式下打印 incoming event
3. 调用 [`Dispatcher.dispatch()`](fastevents/dispatcher.py:72)

## 5. publish 与 send

### [`publish()`](fastevents/bus.py:205)

用于构建新事件并提交到总线。

### [`send()`](fastevents/bus.py:237)

用于发送已经构建好的 [`StandardEvent`](fastevents/events.py:16)。

## 6. 为什么 bus 边界要最小化

因为未来如果要扩展到 remote bus / distributed bus，bus 层不能依赖复杂 Python 对象协议。

所以 bus 只应看到标准化后的发送值，而不是任意业务对象。
