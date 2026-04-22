# Dependency Injection

## 1. 设计目标

FastEvents 的注入不是重量级容器，而是轻量、显式、可推导的参数构造。

入口主要在：

- [`dependency()`](fastevents/subscribers.py:63)
- [`_resolve_parameter()`](fastevents/subscribers.py:108)
- [`_DependencyResolver.build_kwargs()`](fastevents/subscribers.py:239)

当前实现已经把“参数分类决议”和“运行时取值”分成两层：

- [`_resolve_parameter()`](fastevents/subscribers.py:108) 负责判断一个参数属于哪一类
- [`ParameterResolution`](fastevents/subscribers.py:98) 表示单个参数的决议结果
- [`_DependencyResolver.build_kwargs()`](fastevents/subscribers.py:239) 负责在当前事件作用域里真正构造参数值

可以把它理解成：

- 先决定“这个参数应该怎么注”
- 再决定“这次事件里这个参数的值是什么”

## 2. 直接可注入的类型

### 运行时特权类型

- [`RuntimeEvent`](fastevents/events.py:173)

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

解析逻辑在 [`_resolve_payload()`](fastevents/subscribers.py:299)。

这里有一个重要边界：

- 普通 `BaseModel` 仍然可以走 payload model 注入
- [`EventModel`](fastevents/models.py:10) 及其子类现在不再依赖 payload-model 特判
- 如果一个 `BaseModel` 类型声明了自定义 [`_provider()`](fastevents/subscribers.py:199)，则会优先走 provider 注入，而不是 payload 注入

## 3. 推荐的 payload 类型

推荐使用 [`EventModel`](fastevents/models.py:13)。

它的作用是：

- 作为第一方推荐 payload 基类
- 保持与 pydantic 兼容

但要注意，当前 [`EventModel`](fastevents/models.py:10) 的定位已经更偏向“第一方 provider-aware model”：

- 默认情况下，[`EventModel._provider()`](fastevents/models.py:14) 会把 `event.payload` 校验成对应模型
- 如果子类重写 [`_provider()`](fastevents/models.py:14)，则以子类 provider 为准
- 也就是说，[`EventModel`](fastevents/models.py:10) 现在统一走 annotation provider 路径，而不是旧的 payload model 特判路径

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

如果你想让一个自定义类型可以按 annotation 直接注入，给它定义一个静态或类方法 [`_provider()`](fastevents/subscribers.py:199)。

这个方法必须：

- 是 `staticmethod` 或 `classmethod`
- 不接收位置参数
- 返回一个由 [`dependency()`](fastevents/subscribers.py:63) 包装的 provider

解析入口在 [`_get_annotation_provider()`](fastevents/subscribers.py:199)。

当前 provider 的执行已经统一走 [`_DependencyResolver.resolve_dependency()`](fastevents/subscribers.py:281)，因此 provider 可以自然获得：

- 递归依赖解析
- 异步依赖支持
- 循环依赖检测
- 基于 [`DependencyScope`](fastevents/subscribers.py:45) 的 event-scoped 缓存

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

另外，annotation provider 还有一层独立缓存：[`_ANNOTATION_PROVIDER_CACHE`](fastevents/subscribers.py:105)。

它的作用不是缓存 dependency 的求值结果，而是缓存：

- 某个 annotation 对应的 provider [`Dependency`](fastevents/subscribers.py:53) 对象

这样可以让同一个类型的 provider 身份稳定，避免每次参数解析都重复调用 [`_provider()`](fastevents/subscribers.py:199)。

## 8. 当前参数决议模型

当前一个参数大致会被分到以下几类之一，见 [`ParameterResolution`](fastevents/subscribers.py:98)：

- `dependency_call`
- `event`
- `annotation_provider`
- `payload`
- `default`

运行时由 [`_DependencyResolver.build_kwargs()`](fastevents/subscribers.py:239) 根据这个决议结果构造参数。

目前的优先级大致是：

1. 显式 [`DependencyCall`](fastevents/subscribers.py:40)
2. [`RuntimeEvent`](fastevents/events.py:164) / [`StandardEvent`](fastevents/events.py:16)
3. annotation provider
4. payload 注入
5. 普通默认值

## 9. 后续 plan 方向

当前虽然已经有了统一的参数决议函数 [`_resolve_parameter()`](fastevents/subscribers.py:108)，但注册时和运行时仍然都会重新读取 callback 签名。

下一步更自然的方向，是把“参数解析与 provider 决议”固化到 [`HandlerSubscriber`](fastevents/subscribers.py:140) 自己的 plan 上。大意是：

- 在创建 [`HandlerSubscriber`](fastevents/subscribers.py:140) 时，就把这个 callback 的参数注入方案预编译出来
- 运行时不再重新 `inspect.signature(...)` 和 `get_type_hints(...)`
- 而是直接按预编译好的 plan 取值

这样做的好处是：

- provider identity 会更稳定
- 运行时开销更低
- “静态决议”和“动态取值”边界更清楚
- 也比直接把 provider cache 提升为 app 级更符合当前代码分层
