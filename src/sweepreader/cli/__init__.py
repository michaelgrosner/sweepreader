import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="sweepreader",
        description="SweepReader — daily digest for US equity-options market structure",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Fetch, classify, and rebuild the page")
    run_parser.add_argument("--config", default="config.yaml", help="Config file path")
    run_parser.add_argument("--dry-run", action="store_true", help="Skip writes and sends")
    run_parser.add_argument("--render-only", action="store_true", help="Only rebuild the webpage from stored data without fetching or classifying")

    email_parser = subparsers.add_parser("email", help="Send daily email digest")
    email_parser.add_argument("--config", default="config.yaml", help="Config file path")
    email_parser.add_argument("--dry-run", action="store_true", help="Print instead of send")

    backtest_parser = subparsers.add_parser("backtest", help="Re-score history under a config")
    backtest_parser.add_argument("--from", dest="from_date", required=True, help="Start date YYYY-MM-DD")
    backtest_parser.add_argument("--to", dest="to_date", required=True, help="End date YYYY-MM-DD")
    backtest_parser.add_argument("--config", required=True, help="Candidate config file path")

    seed_parser = subparsers.add_parser("seed", help="Backfill historical items for backtesting")
    seed_parser.add_argument("--config", default="config.yaml", help="Config file path")
    seed_parser.add_argument("--months", type=float, default=6.0, help="How far back to seed (default 6)")
    seed_parser.add_argument("--source", default="", help="Comma-separated source ids/parse types (default: all seedable)")
    seed_parser.add_argument("--no-cache", action="store_true", help="Disable the gitignored HTTP fetch cache")
    seed_parser.add_argument("--all-bodies", action="store_true",
                             help="Fetch every MIAX detail body (skip the relevance gate)")
    seed_parser.add_argument("--body-min-relevance", type=int, default=0,
                             help="MIAX: only fetch detail bodies for teasers scoring >= this (keyword gate; tier-E noise always skipped)")

    args = parser.parse_args()

    if args.command == "run":
        from sweepreader.cli.run import cmd_run
        sys.exit(cmd_run(args))
    elif args.command == "email":
        from sweepreader.cli.email_cmd import cmd_email
        sys.exit(cmd_email(args))
    elif args.command == "backtest":
        from sweepreader.cli.backtest import cmd_backtest
        sys.exit(cmd_backtest(args))
    elif args.command == "seed":
        from sweepreader.cli.seed import cmd_seed
        sys.exit(cmd_seed(args))
