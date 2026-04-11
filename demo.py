import asyncio

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RpcContext, RuntimeEvent, dependency, rpc_context
from fastevents.ext.rpc import RpcExtension


app = FastEvents()
app.ex.rpc = RpcExtension()
bus = InMemoryBus()


class ChatTurn(BaseModel):
    session_id: str
    prompt: str

class ChatDone(BaseModel):
    session_id: str
class Token(BaseModel):
    session_id: str
    token: str


class ChatRuntime(BaseModel):
    session_id: str
    prompt: str


class ChatReply(BaseModel):
    session_id: str
    answer: str


def line(text: str = "") -> None:
    print(text)


@dependency
def chat_runtime(data: ChatTurn) -> ChatRuntime:
    return ChatRuntime(session_id=data.session_id, prompt=data.prompt)


@app.on("chat.request", level=0, name="mock-llm")
async def mock_llm(event: RuntimeEvent, rpc: RpcContext = rpc_context(), runtime: ChatRuntime = chat_runtime()) -> None:
    text = (
        f"You said: {runtime.prompt}. "
        "This answer is generated as a stream of terminal events. "
        "Each chunk becomes its own event before it is rendered.\n"
    )
    for chunk in str(text):
        await event.ctx.publish(
            tags="chat.token",
            payload=Token(session_id=runtime.session_id, token=chunk),
        )
        await asyncio.sleep(0.001)
    await rpc.reply(payload=ChatReply(session_id=runtime.session_id, answer=text))


@app.on("chat.token", level=0, name="terminal-renderer")
async def render_token(event: RuntimeEvent, payload: Token) -> None:
    print(payload.token, flush=True, end="")


async def run_prompt(session_id: str, prompt: str) -> None:
    line(f"\n> user: {prompt}")
    reply = await app.ex.rpc.request_one(
        tags="chat.request",
        payload=ChatTurn(session_id=session_id, prompt=prompt),
        model=ChatReply,
    )
    line(f"\n[reply] {reply.answer.strip()}")
    print(f"{reply.session_id} is finished")


async def interactive_demo() -> None:
    await bus.astart(app)
    line("streaming + rpc + dependency demo")
    line("type a message, or /quit to exit")
    counter = 0
    try:
        while True:
            prompt = await asyncio.to_thread(input, "\n> ")
            if prompt.strip() in {"/quit", "/exit"}:
                line("bye")
                return
            counter += 1
            await run_prompt(session_id=f"interactive-{counter}", prompt=prompt)
    except asyncio.exceptions.CancelledError:
        line("bye")
        return


if __name__ == "__main__":
    try:
        asyncio.run(interactive_demo())
    except KeyboardInterrupt:
        pass
