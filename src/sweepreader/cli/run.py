import logging

logger = logging.getLogger(__name__)


def cmd_run(args) -> int:
    logger.info("run: config=%s dry_run=%s", args.config, args.dry_run)
    return 0
