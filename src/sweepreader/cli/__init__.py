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

    from sweepreader.cli.compare_models import DEFAULT_MODELS
    cmp_parser = subparsers.add_parser("compare-models",
                                       help="Compare classifier models, judged blind by Claude")
    cmp_parser.add_argument("--config", default="config.yaml", help="Config file path")
    cmp_parser.add_argument("--stage", choices=["generate", "judge", "report", "all"], default="all")
    cmp_parser.add_argument("--out", default=".cache/model-eval/latest",
                            help="Run directory; every stage resumes from what is already there")
    cmp_parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                            help="Comma-separated OpenRouter model ids")
    cmp_parser.add_argument("--sample", type=int, default=40, help="Items to sample (default 40)")
    cmp_parser.add_argument("--days", type=int, default=60, help="Sample from the last N days (default 60)")
    cmp_parser.add_argument("--seed", type=int, default=1, help="Sampling seed")
    cmp_parser.add_argument("--workers", type=int, default=12, help="Parallel OpenRouter calls")
    cmp_parser.add_argument("--max-usd", type=float, default=0.40,
                            help="Stop generating once this much OpenRouter spend is reached (default 0.40)")
    cmp_parser.add_argument("--judge-model", default="opus", help="Model alias for `claude --model`")
    cmp_parser.add_argument("--judge-workers", type=int, default=8, help="Parallel judge processes")
    cmp_parser.add_argument("--passes", type=int, default=1,
                            help="Judge passes per item, each with a fresh candidate order")

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
    elif args.command == "compare-models":
        from sweepreader.cli.compare_models import cmd_compare_models
        sys.exit(cmd_compare_models(args))
