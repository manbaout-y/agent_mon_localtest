from __future__ import annotations

import argparse

from kvreuse_b5.cli.smoke import main as smoke_main


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run an offline StatePair experiment")
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="use built-in smoke data; real data must be supplied by a future StatePairAdapter",
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    if not args.synthetic:
        parser.error("no real adapter is bundled; pass --synthetic or integrate StatePairAdapter")
    forwarded = ["--config", args.config]
    if args.output:
        forwarded += ["--output", args.output]
    return smoke_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
