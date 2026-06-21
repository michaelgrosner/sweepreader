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

    email_parser = subparsers.add_parser("email", help="Send daily email digest")
    email_parser.add_argument("--config", default="config.yaml", help="Config file path")
    email_parser.add_argument("--dry-run", action="store_true", help="Print instead of send")

    backtest_parser = subparsers.add_parser("backtest", help="Re-score history under a config")
    backtest_parser.add_argument("--from", dest="from_date", required=True, help="Start date YYYY-MM-DD")
    backtest_parser.add_argument("--to", dest="to_date", required=True, help="End date YYYY-MM-DD")
    backtest_parser.add_argument("--config", required=True, help="Candidate config file path")

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
