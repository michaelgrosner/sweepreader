from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sweepreader.deadlines import select_deadlines
from sweepreader.render.page import _collapse
from sweepreader.score import rank_items

if TYPE_CHECKING:
    from sweepreader.config import AppConfig
    from sweepreader.store import StateStore, Store

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"

_TIER_COLORS = {
    "A": "#4F46E5",
    "B": "#3B82F6",
    "C": "#14B8A6",
    "D": "#F59E0B",
    "E": "#9CA3AF",
}


def render_email(
    config: "AppConfig",
    store: "Store",
    state: "StateStore",
    dry_run: bool = False,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    today = now.date()

    last_sent_raw = state.get("last_email_sent_at")
    if last_sent_raw:
        last_sent = datetime.fromisoformat(last_sent_raw)
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
    else:
        last_sent = now - timedelta(days=1)

    items = store.items_as_of(now, config.trailing_days)
    classifications = store.classifications_as_of(now, config_hash=config.config_hash(),
                                                  since=now - timedelta(days=config.trailing_days))

    # Email shows only delta since last send
    delta_items = [i for i in items if i.first_seen_at > last_sent]
    delta_cls = {iid: c for iid, c in classifications.items() if iid in {i.id for i in delta_items}}

    visible, suppressed = rank_items(delta_items, delta_cls, config, now)

    # Collapse cross-posts to one line, same as the page (GROUPING.md §3.4).
    groups = store.groups_as_of(now, since=now - timedelta(days=config.trailing_days)) \
        if config.grouping_enabled else {}
    cards, suppressed_cards = _collapse(visible, suppressed, groups, delta_items, delta_cls, today=today)

    top_items = [c for c in cards if c.cls.tier in ("A", "B")]
    also_items = [c for c in cards if c.cls.tier not in ("A", "B")]

    # Deadlines for email rail: <= 14d, looking back up to max_age_days
    max_age_cutoff = now - timedelta(days=config.max_age_days)
    extended_cls = store.classifications_as_of(now, config_hash=config.config_hash(), since=max_age_cutoff)
    rail_start = today - timedelta(days=1)
    rail_end = today + timedelta(days=14)
    dated_cls = {
        iid: c for iid, c in extended_cls.items()
        if c.deadline_date is not None and rail_start <= c.deadline_date <= rail_end
    }
    missing_ids = set(dated_cls.keys()) - {i.id for i in items}
    email_all_items = list(items)
    if missing_ids:
        extra_items = store.get_items(missing_ids, since=max_age_cutoff)
        email_all_items.extend(extra_items.values())

    item_map = {i.id: i for i in email_all_items}
    deadline_pairs = [
        (item_map[iid], c) for iid, c in dated_cls.items()
        if iid in item_map
    ]
    deadline_items = select_deadlines(deadline_pairs, today=today, max_days=14)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["tier_color"] = lambda t: _TIER_COLORS.get(t, "#9CA3AF")
    env.filters["tier_meter_fill"] = lambda cls: (
        f'<div style="position:absolute;bottom:0;left:0;right:0;'
        f'height:{cls.relevance}%;background:{_TIER_COLORS.get(cls.tier, "#9CA3AF")};'
        f'opacity:0.75;border-radius:2px;"></div>'
    )

    template = env.get_template("email.html")
    html = template.render(
        now=now,
        top_items=top_items,
        also_items=also_items,
        deadline_items=deadline_items,
        suppressed_count=len(suppressed_cards),
        last_sent=last_sent,
        tier_colors=_TIER_COLORS,
        page_url=config.page_url,
    )

    if dry_run:
        print(html)
        logger.info("email dry-run: %d top, %d also, %d suppressed", len(top_items), len(also_items), len(suppressed_cards))
        return html

    _send_email(html, now)
    state.set("last_email_sent_at", now.isoformat())
    return html


def _send_email(html: str, now: datetime) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    to_addr = os.environ.get("SMTP_TO", user)

    if not user or not password:
        raise ValueError("SMTP_USER and SMTP_PASSWORD must be set")

    subject = f"SweepReader · {now.strftime('%Y-%m-%d')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL(host, 465) as server:
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())

    logger.info("Email sent to %s", to_addr)
