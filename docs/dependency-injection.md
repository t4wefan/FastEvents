# Dependency Injection

## 1. 设计目标

FastEvents 的注入不是重量级容器，而是轻量、显式、可推导的参数构造。

入口主要在：

- [`dependency()`](fastevents/subscribers.py:63)
- [`_DependencyResolver.build_kwargs()`](fastevents/subscribers.py:181)

## 2. 直接可注入的类型

### 运行时特权类型

- [`RuntimeEvent`](fastevents/events.py:101)

### payload 快速注入类型

当前直接支持：

- [`dict`](python:1)
- [`list`](python:1)
- [`tuple`](python:1)
- [`str`](python:1)
- [`int`](python:1)
- [`float`](python:1)
- [`bool`](python:1)
- [`bytes`](python:1)
- `pydantic.BaseModel`
- [`EventModel`](fastevents/models.py:13)

解析逻辑在 [`_resolve_payload()`](fastevents/subscribers.py:257)。

## 3. 推荐的 payload 类型

推荐使用 [`EventModel`](fastevents/models.py:13)。

它的作用是：

- 作为第一方推荐 payload 基类
- 保持与 pydantic 兼容

## 4. 函数式依赖

最基础的依赖写法：

```python
from fastevents import RuntimeEvent, dependency


@dependency
def event_id(event: RuntimeEvent) -> str:
    return event.id
```

然后在 handler 中使用：

```python
@app.on("x")
async def handle(value: str = event_id()) -> None:
    ...
```

## 5. 自定义类型注入

如果你想让一个自定义类型可以按 annotation 直接注入，给它定义一个静态 [`_provider()`](fastevents/subscribers.py:158)。

这个静态方法必须返回一个由 [`dependency()`](fastevents/subscribers.py:63) 包装的 provider。

## 6. 错误语义

注入阶段错误现在会被吸收。

也就是说：

- 当前 subscriber 不成立
- 不消费事件
- 传播继续

如果打开 [`debug`](fastevents/app.py:21)，这些错误会被打印出来。

## 7. 缓存语义

依赖解析结果按一次 event dispatch 缓存。

对应作用域对象是 [`DependencyScope`](fastevents/subscribers.py:45)。

这意味着：

- 同一个 handler 内复用 dependency 结果
- 同一个事件的多个 subscriber 也会共享缓存结果
