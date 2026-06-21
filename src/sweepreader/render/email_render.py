from __future__ import annotations

import logging
import smtplib
import os
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sweepreader.score import rank_items

if TYPE_CHECKING:
    from sweepreader.config import AppConfig
    from sweepreader.store import Store, StateStore

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
) -> str:
    last_sent_raw = state.get("last_email_sent_at")
    if last_sent_raw:
        last_sent = datetime.fromisoformat(last_sent_raw)
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
    else:
        last_sent = datetime.now(timezone.utc) - timedelta(days=1)

    now = datetime.now(timezone.utc)
    items = store.items_as_of(now, config.trailing_days)
    classifications = store.classifications_as_of(now, config.model, config.config_hash(),
                                                  since=now - timedelta(days=config.trailing_days))

    # Email shows only delta since last send
    delta_items = [i for i in items if i.first_seen_at > last_sent]
    delta_cls = {iid: c for iid, c in classifications.items() if iid in {i.id for i in delta_items}}

    visible, suppressed = rank_items(delta_items, delta_cls, config, now)

    top_items = [(i, c, s) for i, c, s in visible if c.tier in ("A", "B")]
    also_items = [(i, c, s) for i, c, s in visible if c.tier not in ("A", "B")]

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["tier_color"] = lambda t: _TIER_COLORS.get(t, "#9CA3AF")

    template = env.get_template("email.html")
    html = template.render(
        now=now,
        top_items=top_items,
        also_items=also_items,
        suppressed_count=len(suppressed),
        last_sent=last_sent,
        tier_colors=_TIER_COLORS,
        page_url=config.page_url,
    )

    if dry_run:
        print(html)
        logger.info("email dry-run: %d top, %d also, %d suppressed", len(top_items), len(also_items), len(suppressed))
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
