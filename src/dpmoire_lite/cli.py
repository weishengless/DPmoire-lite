from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def build_command(config_path: str, wait: bool) -> int:
    from .build import run_build

    run_build(Path(config_path), wait=wait)
    return 0


def collect_command(config_path: str, stage: str) -> int:
    from .collect import run_collect

    run_collect(Path(config_path), stage=stage)
    return 0


def init_example_command(target_dir: str) -> int:
    from .inputs import copy_example

    copy_example(Path(target_dir))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="DPmoireLite", description="DPmoireLite VASP dataset builder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Generate and optionally submit calculation folders")
    build.add_argument("config", help="Path to config.yaml")
    build.add_argument("--wait", action="store_true", help="Wait for submitted Slurm jobs to finish")

    collect = subparsers.add_parser("collect", help="Collect a dataset from completed calculations")
    collect.add_argument("config", help="Path to config.yaml")
    collect.add_argument("--stage", required=True, choices=["rlx", "md", "validation"], help="Stage to collect")

    init_example = subparsers.add_parser("init-example", help="Copy the bundled example template")
    init_example.add_argument("target_dir", help="Directory to create from the example template")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "build":
        return build_command(args.config, wait=args.wait)
    if args.command == "collect":
        return collect_command(args.config, stage=args.stage)
    if args.command == "init-example":
        return init_example_command(args.target_dir)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
