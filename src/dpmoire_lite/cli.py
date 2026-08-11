from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from .collect_models import (
    CollectResult,
    CollectStatus,
    DEFAULT_MLFF_COLLECT_MODE,
    MLFFCollectMode,
)


_COLLECT_EXIT_CODES = {
    CollectStatus.COMPLETE: 0,
    CollectStatus.FATAL: 1,
    CollectStatus.DEGRADED: 2,
    CollectStatus.NO_DATA: 3,
}


def build_command(config_path: str, wait: bool) -> int:
    from .build import run_build

    run_build(Path(config_path), wait=wait)
    return 0


def _collect_summary(result: CollectResult, *, output: str) -> str:
    fields = [
        f"collect status={result.status.value}",
        f"frames={result.accepted_frame_count}",
        f"sources={result.source_count}",
        f"complete={result.sources_complete}",
        f"partial={result.sources_partial}",
        f"skipped={result.sources_skipped}",
        f"failed={result.sources_failed}",
        f"output={output}",
    ]
    if result.status is CollectStatus.FATAL:
        diagnostic = " ".join(result.fatal_diagnostic.split())
        fields.append(f"diagnostic={diagnostic}")
    return " ".join(fields)


def collect_command(
    config_path: str,
    stage: str,
    *,
    collection_mode: MLFFCollectMode = DEFAULT_MLFF_COLLECT_MODE,
) -> int:
    from .collect import COLLECT_OUTPUTS, run_collect

    result = run_collect(
        Path(config_path),
        stage=stage,
        collection_mode=collection_mode,
    )
    output = COLLECT_OUTPUTS.get(stage, "unavailable")
    print(_collect_summary(result, output=output), file=sys.stderr)
    return _COLLECT_EXIT_CODES[result.status]


def init_example_command(target_dir: str) -> int:
    from .inputs import copy_example

    copy_example(Path(target_dir))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="DPmoireLite", description="DPmoireLite VASP dataset builder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build",
        help="Generate and optionally submit calculation folders",
        description=(
            "Generate calculation folders. Config init_mlff_mode defaults to manual; "
            "single-job prepares separate bottom/top static inputs and currently "
            "requires submit: false."
        ),
    )
    build.add_argument("config", help="Path to config.yaml")
    build.add_argument(
        "--wait",
        action="store_true",
        help="Temporarily disabled with submit: true; stage: all is unavailable",
    )

    collect = subparsers.add_parser("collect", help="Collect a dataset from completed calculations")
    collect.add_argument("config", help="Path to config.yaml")
    collect.add_argument("--stage", required=True, choices=["rlx", "md", "validation"], help="Stage to collect")
    collect.add_argument(
        "--mlff-collect-mode",
        type=MLFFCollectMode,
        choices=tuple(MLFFCollectMode),
        default=DEFAULT_MLFF_COLLECT_MODE,
        metavar="{seed-aware,full-dedup}",
        help="MLFF collection mode (default: seed-aware)",
    )

    init_example = subparsers.add_parser("init-example", help="Copy the bundled example template")
    init_example.add_argument("target_dir", help="Directory to create from the example template")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "build":
        return build_command(args.config, wait=args.wait)
    if args.command == "collect":
        return collect_command(
            args.config,
            stage=args.stage,
            collection_mode=args.mlff_collect_mode,
        )
    if args.command == "init-example":
        return init_example_command(args.target_dir)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
