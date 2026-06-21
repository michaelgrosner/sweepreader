import logging

logger = logging.getLogger(__name__)


def cmd_backtest(args) -> int:
    logger.info("backtest: from=%s to=%s config=%s", args.from_date, args.to_date, args.config)
    return 0
