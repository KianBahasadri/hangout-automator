"""Admin cost-monitoring helpers (local estimates + vendor deep links).

Phase A of KIAN-535: Twilio usage from `message_logs`, Azure/Cloudflare as
labels + console links when configured. Live vendor billing APIs are optional
later; nothing here should break the app if a vendor is down.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import MessageDirection, MessageLog

# Portal deep links when live Cost Management API is not wired.
AZURE_COST_PORTAL = (
    "https://portal.azure.com/#view/Microsoft_Azure_CostManagement/Menu/~/costanalysis"
)
AZURE_RG_PORTAL = (
    "https://portal.azure.com/#@/resource/subscriptions/{subscription_id}"
    "/resourceGroups/{resource_group}/overview"
)
TWILIO_CONSOLE = "https://console.twilio.com/us1/monitor/logs/sms"
CLOUDFLARE_BILLING = "https://dash.cloudflare.com/?to=/:account/billing"


@dataclass(frozen=True)
class SmsPeriodStats:
    """SMS traffic for one time window, derived only from local `message_logs`."""

    key: str
    label: str
    outbound_ok: int
    outbound_fail: int
    inbound: int
    billable: int  # outbound_ok + inbound (Twilio charges both directions)
    estimated_usd: float | None


@dataclass(frozen=True)
class CostCard:
    """One vendor card on the Admin → Costs panel."""

    name: str
    source: str  # estimate | link | unavailable
    summary: str
    detail_lines: tuple[str, ...]
    link_url: str | None
    link_label: str | None
    periods: tuple[SmsPeriodStats, ...] = ()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _period_starts(now: datetime) -> list[tuple[str, str, datetime]]:
    mtd = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return [
        ("mtd", "Month to date", mtd),
        ("d7", "Last 7 days", now - timedelta(days=7)),
        ("d30", "Last 30 days", now - timedelta(days=30)),
    ]


def sms_period_stats(
    db: Session,
    *,
    since: datetime,
    price_per_message: float | None,
    key: str,
    label: str,
) -> SmsPeriodStats:
    """Aggregate SMS rows with `created_at >= since` (UTC-aware)."""
    row = (
        db.query(
            func.coalesce(
                func.sum(
                    case(
                        (
                            (MessageLog.direction == MessageDirection.outbound)
                            & (MessageLog.success.is_(True)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (MessageLog.direction == MessageDirection.outbound)
                            & (MessageLog.success.is_(False)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (MessageLog.direction == MessageDirection.inbound, 1),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .filter(MessageLog.created_at >= since)
        .one()
    )
    outbound_ok = int(row[0] or 0)
    outbound_fail = int(row[1] or 0)
    inbound = int(row[2] or 0)
    billable = outbound_ok + inbound
    estimated = (
        round(billable * price_per_message, 4) if price_per_message is not None else None
    )
    return SmsPeriodStats(
        key=key,
        label=label,
        outbound_ok=outbound_ok,
        outbound_fail=outbound_fail,
        inbound=inbound,
        billable=billable,
        estimated_usd=estimated,
    )


def twilio_cost_card(db: Session, settings: Settings | None = None) -> CostCard:
    settings = settings or get_settings()
    price = settings.twilio_sms_price_estimate
    now = _utc_now()
    periods = tuple(
        sms_period_stats(
            db,
            since=since,
            price_per_message=price,
            key=key,
            label=label,
        )
        for key, label, since in _period_starts(now)
    )
    mtd = periods[0]
    if price is not None:
        summary = (
            f"~${mtd.estimated_usd:.2f} MTD estimate "
            f"({mtd.billable} billable messages × ${price:.4f})"
        )
        source = "estimate"
    else:
        summary = (
            f"{mtd.billable} billable messages MTD "
            f"({mtd.outbound_ok} out · {mtd.inbound} in). "
            "Set TWILIO_SMS_PRICE_ESTIMATE for a $ estimate."
        )
        source = "estimate"

    detail = (
        f"Provider: {settings.sms_provider}",
        "Source: local message_logs (not Twilio Usage API)",
        f"As of {now.strftime('%Y-%m-%d %H:%M')} UTC",
    )
    return CostCard(
        name="Twilio (SMS)",
        source=source,
        summary=summary,
        detail_lines=detail,
        link_url=TWILIO_CONSOLE,
        link_label="Open Twilio SMS logs",
        periods=periods,
    )


def azure_cost_card(settings: Settings | None = None) -> CostCard:
    settings = settings or get_settings()
    rg = (settings.azure_resource_group or "").strip()
    sub = (settings.azure_subscription_id or "").strip()
    if rg and sub:
        link = AZURE_RG_PORTAL.format(subscription_id=sub, resource_group=rg)
        return CostCard(
            name="Azure",
            source="link",
            summary=f"Resource group `{rg}`",
            detail_lines=(
                "Live Cost Management API is not wired yet.",
                "Open Azure Cost Management for spend.",
            ),
            link_url=link,
            link_label="Open resource group",
        )
    if rg:
        return CostCard(
            name="Azure",
            source="link",
            summary=f"Resource group `{rg}`",
            detail_lines=(
                "Live Cost Management API is not wired yet.",
                "Set AZURE_SUBSCRIPTION_ID for a direct portal link.",
            ),
            link_url=AZURE_COST_PORTAL,
            link_label="Open Azure Cost Management",
        )
    return CostCard(
        name="Azure",
        source="unavailable",
        summary="Not configured",
        detail_lines=(
            "Set AZURE_RESOURCE_GROUP (and optionally AZURE_SUBSCRIPTION_ID)",
            "to label this deployment and deep-link Cost Management.",
            "Live billing pull is Phase B.",
        ),
        link_url=AZURE_COST_PORTAL,
        link_label="Open Azure Cost Management",
    )


def cloudflare_cost_card() -> CostCard:
    return CostCard(
        name="Cloudflare",
        source="unavailable",
        summary="No usage API wired",
        detail_lines=(
            "Tunnel / DNS are typically free-tier for this deploy.",
            "Account billing is only in the Cloudflare dashboard.",
        ),
        link_url=CLOUDFLARE_BILLING,
        link_label="Open Cloudflare billing",
    )


def admin_cost_cards(db: Session, settings: Settings | None = None) -> list[CostCard]:
    settings = settings or get_settings()
    return [
        twilio_cost_card(db, settings),
        azure_cost_card(settings),
        cloudflare_cost_card(),
    ]
