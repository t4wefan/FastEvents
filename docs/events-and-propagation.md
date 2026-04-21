# Events and Propagation

## 1. 事件长什么样

标准事件定义在 [`StandardEvent`](fastevents/events.py:16)。

最重要的字段：

- `tags`：决定谁会匹配到这个事件
- `payload`：业务数据
- `meta`：协议元数据或跟踪信息

## 2. 事件怎么发送

最常见方式：

- [`app.publish()`](fastevents/app.py:83)
- [`event.ctx.publish()`](fastevents/events.py:123)

### 推荐使用 [`app.publish()`](fastevents/app.py:83)

它是最清晰的应用侧发送边界。

### 在 handler 内使用 [`event.ctx.publish()`](fastevents/events.py:123)

这适合发布 follow-up event。

## 3. payload 的编码规则

app 层支持更丰富的值，编码入口在 [`encode_app_value()`](fastevents/events.py:55)。

当前支持：

- 标量：`None` / `bool` / `int` / `float` / `str` / `bytes`
- `list`
- `tuple`
- `dict[str, ...]`
- dataclass
- `pydantic.BaseModel`
- [`EventModel`](fastevents/models.py:13)

其中：

- `tuple` 会被编码成 `{"tuple": [...]}`
- 非标量复杂结构会编码成 JSON string

运行时读取时，解码逻辑在：

- [`decode_event_value()`](fastevents/events.py:64)
- [`decode_event_meta()`](fastevents/events.py:79)

## 4. 传播模型

传播逻辑在 [`Dispatcher.dispatch()`](fastevents/dispatcher.py:72)。

规则如下：

- 按 `level` 升序执行
- 同一 level 的 subscriber 并发运行
- `level < 0` 适合观察者
- `level >= 0` 适合业务处理和 fallback

## 5. fallback

如果主处理器想明确放弃处理，可以抛出 [`SessionNotConsumed`](fastevents/exceptions.py:17)。

这样 dispatcher 会继续向更高 `level` 传播。

## 6. 调试传播过程

打开 [`FastEvents(debug=True)`](fastevents/app.py:21) 后，你可以看到：

- incoming / outgoing event
- 匹配到哪些 subscriber
- 每个 level 如何传播
- 哪些注入错误被吸收
