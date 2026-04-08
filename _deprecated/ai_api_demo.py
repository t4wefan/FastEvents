from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RuntimeEvent, SessionNotConsumed


app_events = FastEvents()
bus = InMemoryBus()


class ChatRequest(BaseModel):
    session_id: str
    message: str
    user_tier: str = "free"


class ModerationDecision(BaseModel):
    allowed: bool
    reason: str


class RetrievalRequest(BaseModel):
    query: str


class RetrievalResult(BaseModel):
    snippets: list[str]


class WorkflowResponse(BaseModel):
    session_id: str
    answer: str
    route: str


class RunView(BaseModel):
    session_id: str
    message: str
    answer: str | None = None
    route: str | None = None
    events: list[str]


@dataclass
class RunState:
    session_id: str
    message: str
    events: list[str] = field(default_factory=list)
    answer: str | None = None
    route: str | None = None


RUNS: dict[str, RunState] = {}


def record(session_id: str, message: str) -> None:
    RUNS.setdefault(session_id, RunState(session_id=session_id, message=message)).events.append(message)


@app_events.on("ai.*", level=-1, name="ai-audit")
async def audit_ai_events(event: RuntimeEvent) -> None:
    session_id = str(event.payload.get("session_id", "unknown")) if isinstance(event.payload, dict) else "unknown"
    record(session_id, f"audit:{','.join(event.tags)}")


@app_events.on("moderation.check", level=0, name="moderation-service")
async def moderation_service(event: RuntimeEvent, payload: dict) -> None:
    message = str(payload.get("message", ""))
    lowered = message.lower()
    banned = any(word in lowered for word in ("hack bank", "make bomb", "steal password"))
    decision = {"allowed": not banned, "reason": "blocked" if banned else "ok"}
    record(str(payload.get("session_id", "unknown")), f"moderation:{decision['reason']}")
    await event.ctx.reply(payload=decision)


@app_events.on("knowledge.retrieve", level=0, name="knowledge-retriever")
async def knowledge_retriever(event: RuntimeEvent, data: RetrievalRequest) -> None:
    query = data.query.lower()
    snippets: list[str]
    if "pricing" in query:
        snippets = [
            "Pro plan adds workflow memory and priority execution.",
            "Team plan adds audit and shared tool access.",
        ]
    elif "weather" in query:
        snippets = [
            "Weather tool is simulated in this demo.",
            "Current business rule: mention that live weather is unavailable.",
        ]
    else:
        snippets = [
            "fastevents models workflows as events, layers, fallback, and request/reply.",
            "Use negative levels for audit and observation subscribers.",
        ]
    await event.ctx.reply(payload={"snippets": snippets})


@app_events.on("ai.chat", level=0, name="primary-ai-workflow")
async def primary_ai_workflow(event: RuntimeEvent, payload: dict) -> None:
    session_id = str(payload["session_id"])
    message = str(payload["message"])
    record(session_id, "route:primary")

    if payload.get("user_tier") == "legacy":
        raise SessionNotConsumed()

    moderation_event = await bus.request(
        tags="moderation.check",
        payload={"session_id": session_id, "message": message},
        timeout=1,
    )
    moderation = ModerationDecision.model_validate(moderation_event.payload)
    if not moderation.allowed:
        await event.ctx.reply(
            payload={
                "session_id": session_id,
                "answer": "I cannot help with that request.",
                "route": "blocked",
            }
        )
        return

    retrieval_event = await bus.request(
        tags="knowledge.retrieve",
        payload={"query": message},
        timeout=1,
    )
    retrieval = RetrievalResult.model_validate(retrieval_event.payload)

    answer = (
        f"Answer: {retrieval.snippets[0]} "
        f"Extra context: {retrieval.snippets[1]}"
    )
    record(session_id, "answer:primary")
    await event.ctx.publish(
        tags="ai.answer.generated",
        payload={"session_id": session_id, "route": "primary"},
    )
    await event.ctx.reply(
        payload={
            "session_id": session_id,
            "answer": answer,
            "route": "primary",
        }
    )


@app_events.on("ai.chat", level=1, name="legacy-ai-fallback")
async def legacy_ai_workflow(event: RuntimeEvent, payload: dict) -> None:
    session_id = str(payload["session_id"])
    record(session_id, "route:fallback")
    await event.ctx.reply(
        payload={
            "session_id": session_id,
            "answer": "Legacy fallback answer: please upgrade for retrieval-backed responses.",
            "route": "fallback",
        }
    )


@app_events.on("ai.answer.generated", level=0, name="persist-answer")
async def persist_answer(event: RuntimeEvent, payload: dict) -> None:
    session_id = str(payload["session_id"])
    record(session_id, f"generated:{payload['route']}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bus.astart(app_events)
    try:
        yield
    finally:
        await bus.astop()


api = FastAPI(title="fastevents AI workflow demo", lifespan=lifespan)


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@api.post("/ai/chat", response_model=WorkflowResponse)
async def ai_chat(request: ChatRequest) -> WorkflowResponse:
    RUNS[request.session_id] = RunState(session_id=request.session_id, message=request.message)
    reply = await bus.request(tags="ai.chat", payload=request.model_dump(), timeout=2)
    result = WorkflowResponse.model_validate(reply.payload)
    state = RUNS[request.session_id]
    state.answer = result.answer
    state.route = result.route
    return result


@api.get("/ai/runs/{session_id}", response_model=RunView)
async def get_run(session_id: str) -> RunView:
    state = RUNS.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunView(
        session_id=state.session_id,
        message=state.message,
        answer=state.answer,
        route=state.route,
        events=state.events,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ai_api_demo:api", host="127.0.0.1", port=8000, reload=False)
