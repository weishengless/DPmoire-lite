from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .init_mlff_contract import (
    INIT_MLFF_LAUNCH_FUNCTION,
    INIT_MLFF_RUN_MARKER,
    INIT_MLFF_RUN_MARKER_LINE,
    INIT_MLFF_SUBMIT_ADAPTER_SCHEMA,
)
from .init_mlff import (
    InitMlffCalculation,
    InitMlffCalculationAdapter,
    InitMlffWorkflow,
)
from .inputs import PreparedSource, verify_prepared_source
from .manifest import Manifest

_FUNCTION_HEADER = f"{INIT_MLFF_LAUNCH_FUNCTION}() {{"
_FUNCTION_DECLARATION = re.compile(
    rf"^(?:function[ \t]+{INIT_MLFF_LAUNCH_FUNCTION}"
    r"(?:[ \t]*\([ \t]*\))?"
    rf"|{INIT_MLFF_LAUNCH_FUNCTION}[ \t]*\([ \t]*\))"
    r"[ \t]*(?:\{|$)"
)
_RESERVED_ROOT_NAMES = {
    "bottom",
    "manifest.yaml",
    "ml_ab",
    "ml_abn",
    "ml_ff",
    "ml_ffn",
    "top",
}


class InitMlffSubmitTemplateError(ValueError):
    """The explicit single-job submit-template contract is invalid."""


@dataclass(frozen=True)
class PreparedInitMlffSubmitAdapter:
    source: PreparedSource
    generated_name: str
    rendered_bytes: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.rendered_bytes)

    def audit_record(self) -> dict[str, object]:
        return {
            "schema": INIT_MLFF_SUBMIT_ADAPTER_SCHEMA,
            "marker": INIT_MLFF_RUN_MARKER_LINE,
            "launch_function": INIT_MLFF_LAUNCH_FUNCTION,
            "generated_script": {
                "name": self.generated_name,
                "size": self.size,
                "sha256": self.sha256,
            },
        }


def prepare_init_mlff_submit_adapter(
    source: PreparedSource,
) -> PreparedInitMlffSubmitAdapter:
    """Validate and render one immutable marked Bash submit template."""
    generated_name = source.path.name
    if generated_name.rstrip(" .").casefold() in _RESERVED_ROOT_NAMES:
        raise InitMlffSubmitTemplateError(
            "single-job derived submit script name conflicts with a reserved "
            f"init_mlff root entry: {generated_name!r}"
        )

    payload = _read_prepared_source(source)
    lines = payload.splitlines(keepends=True)
    if not lines:
        raise InitMlffSubmitTemplateError(
            "single-job submit template is empty; a Bash template is required"
        )
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InitMlffSubmitTemplateError(
            "single-job submit template must be valid UTF-8"
        ) from exc
    if b"\x00" in payload:
        raise InitMlffSubmitTemplateError(
            "single-job submit template must not contain NUL bytes"
        )

    bodies = [_line_body(line).decode("utf-8") for line in lines]
    if not _is_bash_shebang(bodies[0]):
        raise InitMlffSubmitTemplateError(
            "single-job submit template must begin with a Bash shebang"
        )
    _validate_slurm_directive_positions(bodies)

    marker_occurrences = payload.count(INIT_MLFF_RUN_MARKER.encode("ascii"))
    if marker_occurrences != 1:
        raise InitMlffSubmitTemplateError(
            "single-job submit template requires exactly one insertion marker "
            f"{INIT_MLFF_RUN_MARKER!r}; found {marker_occurrences}"
        )
    marker_indices = [
        index for index, body in enumerate(bodies) if body == INIT_MLFF_RUN_MARKER_LINE
    ]
    if len(marker_indices) != 1:
        raise InitMlffSubmitTemplateError(
            "single-job submit template must use the exact marker line "
            f"{INIT_MLFF_RUN_MARKER_LINE!r}"
        )
    marker_index = marker_indices[0]

    function_indices = [
        index for index, body in enumerate(bodies) if body == _FUNCTION_HEADER
    ]
    if len(function_indices) != 1:
        raise InitMlffSubmitTemplateError(
            "single-job submit template requires exactly one launch function "
            f"declared as {_FUNCTION_HEADER!r}; found {len(function_indices)}"
        )
    function_index = function_indices[0]
    definition_indices = [
        index
        for index, body in enumerate(bodies)
        if _FUNCTION_DECLARATION.match(body.strip())
    ]
    if definition_indices != [function_index]:
        raise InitMlffSubmitTemplateError(
            "single-job submit template must define the launch function exactly "
            "once using the documented declaration"
        )
    if marker_index <= function_index:
        raise InitMlffSubmitTemplateError(
            "single-job insertion marker must appear after the launch function"
        )
    closing_indices = [
        index
        for index in range(function_index + 1, marker_index)
        if bodies[index].strip() == "}"
    ]
    if not closing_indices:
        raise InitMlffSubmitTemplateError(
            "single-job launch function requires a standalone closing '}' before "
            "the insertion marker"
        )
    if len(closing_indices) != 1:
        raise InitMlffSubmitTemplateError(
            "single-job launch function contract is structurally ambiguous; "
            "the marker must be top-level"
        )
    closing_index = closing_indices[0]
    if any(
        _is_executable_line(body)
        for body in bodies[closing_index + 1 : marker_index]
    ):
        raise InitMlffSubmitTemplateError(
            "single-job insertion marker must be the first top-level executable "
            "line after the launch function"
        )
    if any(_is_executable_line(body) for body in bodies[marker_index + 1 :]):
        raise InitMlffSubmitTemplateError(
            "single-job insertion marker must be the final executable top-level line"
        )

    marker_ending = _line_ending(lines[marker_index])
    newline = marker_ending or _first_line_ending(lines) or b"\n"
    replacement = newline.join(
        line.encode("utf-8") for line in _generated_adapter_lines()
    )
    if marker_ending:
        replacement += marker_ending
    rendered = b"".join(lines[:marker_index]) + replacement + b"".join(
        lines[marker_index + 1 :]
    )
    return PreparedInitMlffSubmitAdapter(
        source=source,
        generated_name=generated_name,
        rendered_bytes=rendered,
        sha256=hashlib.sha256(rendered).hexdigest(),
    )


class BashFunctionInitMlffAdapter:
    """Invoke the exported template launch function in each phase directory."""

    def run(self, request: InitMlffCalculation) -> int:
        environment = os.environ.copy()
        environment.update(
            {
                "DPMOIRE_PHASE": request.phase,
                "DPMOIRE_PHASE_NAME": request.name,
                "DPMOIRE_PHASE_ROLE": request.role,
            }
        )
        print(
            "DPmoireLite init_mlff "
            f"phase={request.phase} role={request.role} "
            f"directory={request.directory}",
            file=sys.stderr,
            flush=True,
        )
        result = subprocess.run(
            ["bash", "-c", INIT_MLFF_LAUNCH_FUNCTION],
            cwd=str(request.directory),
            env=environment,
            check=False,
        )
        return result.returncode


def run_init_mlff_submit_workflow(
    work_dir: Path,
    *,
    adapter: InitMlffCalculationAdapter | None = None,
) -> Manifest:
    calculation_adapter = adapter
    if calculation_adapter is None:
        calculation_adapter = BashFunctionInitMlffAdapter()
    return InitMlffWorkflow(work_dir).run(calculation_adapter)


def _read_prepared_source(source: PreparedSource) -> bytes:
    verify_prepared_source(source)
    payload = source.path.read_bytes()
    if (
        len(payload) != source.size
        or hashlib.sha256(payload).hexdigest() != source.sha256
    ):
        raise RuntimeError(
            f"Prepared source identity changed since preflight for {source.path}"
        )
    return payload


def _line_body(line: bytes) -> bytes:
    ending = _line_ending(line)
    return line[: -len(ending)] if ending else line


def _line_ending(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return b"\r\n"
    if line.endswith(b"\n"):
        return b"\n"
    return b""


def _first_line_ending(lines: list[bytes]) -> bytes:
    for line in lines:
        ending = _line_ending(line)
        if ending:
            return ending
    return b""


def _validate_slurm_directive_positions(lines: list[str]) -> None:
    executable_seen = False
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#SBATCH"):
            if executable_seen:
                raise InitMlffSubmitTemplateError(
                    "single-job Slurm directives must precede executable template content"
                )
            continue
        if stripped.startswith("#"):
            continue
        executable_seen = True


def _is_bash_shebang(line: str) -> bool:
    if not line.startswith("#!"):
        return False
    try:
        command = shlex.split(line[2:].strip(), posix=True)
    except ValueError:
        return False
    if not command:
        return False
    executable = PurePosixPath(command[0]).name
    if executable == "bash":
        return True
    if executable != "env":
        return False
    for argument in command[1:]:
        if argument == "--" or argument.startswith("-") or "=" in argument:
            continue
        return PurePosixPath(argument).name == "bash"
    return False


def _is_executable_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped and not stripped.startswith("#"))


def _generated_adapter_lines() -> tuple[str, ...]:
    return (
        "# DPMOIRE-LITE:BEGIN GENERATED SINGLE-JOB ADAPTER",
        f"if ! declare -F {INIT_MLFF_LAUNCH_FUNCTION} >/dev/null 2>&1; then",
        "    printf '%s\\n' 'DPmoireLite: required synchronous launch function "
        f"{INIT_MLFF_LAUNCH_FUNCTION} is unavailable' >&2",
        "    exit 2",
        "fi",
        f"export -f {INIT_MLFF_LAUNCH_FUNCTION}",
        'if [ -z "${SLURM_SUBMIT_DIR:-}" ]; then',
        "    printf '%s\\n' 'DPmoireLite: submit from the generated init_mlff "
        "directory so SLURM_SUBMIT_DIR identifies the workflow' >&2",
        "    exit 2",
        "fi",
        'DPmoireLite run-init-mlff --work-dir "$SLURM_SUBMIT_DIR/.."',
        "_dpmoire_lite_status=$?",
        'if [ "$_dpmoire_lite_status" -ne 0 ]; then',
        '    exit "$_dpmoire_lite_status"',
        "fi",
        "# DPMOIRE-LITE:END GENERATED SINGLE-JOB ADAPTER",
    )
