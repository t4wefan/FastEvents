# RPC Extension

## 1. RPC 不是核心模型

FastEvents 的核心不是 request/reply，而是事件传播。

RPC 作为第一方扩展存在于：

- [`fastevents/ext/rpc.py`](fastevents/ext/rpc.py)

## 2. 入口对象

### [`RpcExtension`](fastevents/ext/rpc.py:155)

用于 app 侧发起 request。

### [`RpcContext`](fastevents/ext/rpc.py:107)

用于 handler 内进行 reply。

### [`rpc_context()`](fastevents/ext/rpc.py:150)

这是一个 dependency，用来把 [`RpcContext`](fastevents/ext/rpc.py:107) 注入到 handler。

## 3. 最常用 API

### [`request_one()`](fastevents/ext/rpc.py:243)

等待第一条 reply。

### [`request()`](fastevents/ext/rpc.py:283)

收集多条 reply。

### [`request_stream()`](fastevents/ext/rpc.py:188)

返回 reply stream。

## 4. handler 内 reply

常见写法：

```python
@app.on("user.lookup")
async def lookup(rpc = rpc_context()) -> None:
    await rpc.reply(payload={"user_id": 1, "name": "Alice"})
```

## 5. 超时与错误

相关错误包括：

- [`RpcRequestTimeoutError`](fastevents/ext/rpc.py:26)
- [`RpcReplyNotAvailableError`](fastevents/ext/rpc.py:30)

## 6. 为什么 RPC 适合作为扩展

因为它依赖的是：

- 普通事件发送
- 临时 stream subscriber
- dependency 注入

而不是 dispatcher 的特殊分支。

这说明 FastEvents 的核心边界足够清晰，扩展协议可以建立在公开能力之上。
