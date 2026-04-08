from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RuntimeEvent, SessionNotConsumed


app = FastEvents()


class OrderSubmitted(BaseModel):
    order_id: str
    user_id: str
    amount: float
    region: str


class FraudDecision(BaseModel):
    order_id: str
    approved: bool
    reason: str


class PaymentCommand(BaseModel):
    order_id: str
    amount: float
    channel: str


class PaymentResult(BaseModel):
    order_id: str
    success: bool
    channel: str
    receipt_id: str | None = None


class SmsQuoteRequest(BaseModel):
    region: str


@dataclass
class Timeline:
    entries: list[str]

    def add(self, message: str) -> None:
        self.entries.append(message)
        print(message)


timeline = Timeline(entries=[])


def section(title: str) -> None:
    print(f"\n=== {title} ===")


@app.on("order.*", level=-1, name="audit-order-events")
async def audit_order_events(event: RuntimeEvent) -> None:
    timeline.add(f"[audit] tags={event.tags} payload={event.payload}")


@app.on(("order.submitted", "vip"), level=-1, name="vip-observer")
async def watch_vip_orders(event: RuntimeEvent) -> None:
    timeline.add(f"[observe] vip order detected order_id={event.payload['order_id']}")


@app.on(["ops.alert", ("payment.failed", "high_value")], level=0, name="ops-dsl-handler")
async def ops_dashboard_listener(event: RuntimeEvent) -> None:
    timeline.add(f"[dsl] ops dashboard matched tags={event.tags}")


@app.on("fraud.check", level=0, name="fraud-engine")
async def fraud_engine(event: RuntimeEvent, data: OrderSubmitted) -> None:
    approved = data.region != "blocked"
    reason = "ok" if approved else "region_blocked"
    timeline.add(f"[fraud] order={data.order_id} approved={approved} reason={reason}")
    await event.ctx.reply(payload={"order_id": data.order_id, "approved": approved, "reason": reason})


@app.on(("order.submitted", "-legacy"), level=0, name="primary-order-orchestrator")
async def primary_order_orchestrator(event: RuntimeEvent, data: OrderSubmitted) -> None:
    timeline.add(f"[order] primary orchestrator accepted order={data.order_id}")

    fraud_reply = await event.ctx.publish  # type: ignore[assignment]
    _ = fraud_reply
    decision_event = await BUS.request(
        tags="fraud.check",
        payload=data.model_dump(),
        timeout=1,
    )
    decision = FraudDecision.model_validate(decision_event.payload)
    if not decision.approved:
        timeline.add(f"[order] fraud rejected order={data.order_id}")
        await event.ctx.publish(
            tags=["order.rejected", "ops.alert"],
            payload={"order_id": data.order_id, "reason": decision.reason},
        )
        return

    channel = "card"
    if data.amount >= 1000:
        channel = "manual_review"
    await event.ctx.publish(
        tags=["payment.command", "high_value"] if data.amount >= 1000 else ["payment.command"],
        payload={"order_id": data.order_id, "amount": data.amount, "channel": channel},
    )


@app.on("order.submitted", level=1, name="legacy-fallback")
async def legacy_order_fallback(event: RuntimeEvent, payload: dict) -> None:
    timeline.add(f"[fallback] legacy order fallback for order={payload.get('order_id', 'unknown')}")
    await event.ctx.publish(
        tags=["payment.command", "legacy"],
        payload={
            "order_id": payload.get("order_id", "legacy-order"),
            "amount": payload.get("amount", 0),
            "channel": "cashier",
        },
    )


@app.on(("payment.command", "-legacy"), level=0, name="payment-processor")
async def payment_processor(event: RuntimeEvent, data: PaymentCommand) -> None:
    timeline.add(f"[payment] charge order={data.order_id} via={data.channel}")
    await event.ctx.publish(
        tags=["payment.succeeded"],
        payload={
            "order_id": data.order_id,
            "success": True,
            "channel": data.channel,
            "receipt_id": f"rcpt-{data.order_id}",
        },
    )


@app.on("payment.command", level=1, name="legacy-payment-fallback")
async def legacy_payment_processor(event: RuntimeEvent, payload: dict) -> None:
    timeline.add(f"[payment-fallback] offline payment for order={payload['order_id']}")
    await event.ctx.publish(
        tags="payment.succeeded",
        payload={
            "order_id": payload["order_id"],
            "success": True,
            "channel": payload.get("channel", "cashier"),
            "receipt_id": f"offline-{payload['order_id']}",
        },
    )


@app.on("payment.succeeded", level=0, name="payment-success-handler")
async def payment_success(event: RuntimeEvent, data: PaymentResult) -> None:
    timeline.add(f"[success] payment success order={data.order_id} receipt={data.receipt_id}")
    await event.ctx.publish(
        tags="notification.requested",
        payload={"order_id": data.order_id, "channel": data.channel},
    )


@app.on("notification.requested", level=0, name="notification-orchestrator")
async def notification_orchestrator(event: RuntimeEvent, payload: dict) -> None:
    quote = await BUS.request(
        tags="sms.quote",
        payload={"region": "cn"},
        timeout=1,
    )
    timeline.add(f"[notify] sms quote={quote.payload['price']} for order={payload['order_id']}")
    await event.ctx.publish(
        tags="notification.sent",
        payload={"order_id": payload["order_id"], "status": "sent"},
    )


@app.on("sms.quote", level=0, name="sms-pricing")
async def sms_quote(event: RuntimeEvent, data: SmsQuoteRequest) -> None:
    price = 0.03 if data.region == "cn" else 0.08
    timeline.add(f"[quote] sms quote requested region={data.region}")
    await event.ctx.reply(payload={"region": data.region, "price": price})


BUS = InMemoryBus()


async def run_demo() -> None:
    await BUS.astart(app)
    try:
        section("Live Stream For Notifications")
        async with BUS.listen("notification.sent", level=-1) as stream:
            await BUS.publish(
                tags=["order.submitted", "vip"],
                payload={
                    "order_id": "ord-1001",
                    "user_id": "u-7",
                    "amount": 188.0,
                    "region": "cn",
                },
            )
            notification = await asyncio.wait_for(stream.__anext__(), timeout=1)
            timeline.add(f"[listen] live notification stream -> {notification.payload}")

        section("Tag DSL And Negative Pattern")
        await BUS.publish(
            tags=["order.submitted", "vip", "legacy"],
            payload={
                "order_id": "ord-legacy",
                "amount": 20.0,
            },
        )
        await BUS.publish(
            tags=["payment.failed", "high_value"],
            payload={"order_id": "ord-risk", "reason": "manual_review_timeout"},
        )
        await BUS.publish(tags="ops.alert", payload={"message": "warehouse delayed"})
        await asyncio.sleep(0.05)

        section("Request Reply As Business Capability")
        quote = await BUS.request(tags="sms.quote", payload={"region": "us"}, timeout=1)
        timeline.add(f"[request] direct sms quote -> {quote.payload}")

        section("Business Summary")
        print(f"timeline events captured: {len(timeline.entries)}")
    finally:
        await BUS.astop()


if __name__ == "__main__":
    asyncio.run(run_demo())
