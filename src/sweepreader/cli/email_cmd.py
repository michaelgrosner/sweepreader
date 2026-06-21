from __future__ import annotations

import logging

from sweepreader.config import load_config
from sweepreader.render.email_render import render_email
from sweepreader.store import Store, StateStore

logger = logging.getLogger(__name__)


def cmd_email(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    store = Store()
    state = StateStore()

    render_email(config, store, state, dry_run=args.dry_run)

    if not args.dry_run:
        state.save()

    return 0
