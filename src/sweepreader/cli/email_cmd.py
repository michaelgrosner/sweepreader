import logging

logger = logging.getLogger(__name__)


def cmd_email(args) -> int:
    logger.info("email: config=%s dry_run=%s", args.config, args.dry_run)
    return 0
