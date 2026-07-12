from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import warnings as pywarnings

import numpy as np
from ase.constraints import FixScaled
from ase.io.vasp import read_vasp

from .config import DPmoireLiteConfig
from .incar import IncarAnalysis, parse_incar
from .inputs import get_ordered_elements, read_enmax, resolve_potcar_dir
from .mlab import MlabParseResult, parse_mlab
from .manifest import Manifest, read_manifest
from .provenance import (
    CELL_ATOL,
    CELL_RTOL,
    StructureIdentity,
    inplane_supercell_relation,
    structure_identity,
    structure_identity_differences,
)
from .structures import StructureHandler, generate_stackings, validate_cartesian_z_slab_cell


VASP_RELAXATION_CONVERGED_PHRASE = (
    "reached required accuracy - stopping structural energy minimisation"
)


@dataclass(frozen=True)
class PreflightDiagnostic:
    domain: str
    path: Path
    reason: str
    tag: str | None = None
    block: str | None = None

    def format(self) -> str:
        context = []
        if self.tag is not None:
            context.append(f"tag={self.tag}")
        if self.block is not None:
            context.append(f"block={self.block}")
        suffix = f" ({', '.join(context)})" if context else ""
        return f"[{self.domain}] {self.path.as_posix()}{suffix}: {self.reason}"


@dataclass(frozen=True)
class BuildPreflightResult:
    stage: int | str
    structures: StructureHandler | None
    stackings: tuple[tuple[int, int], ...] = ()
    rcut: float | None = None
    templates: dict[str, IncarAnalysis] = field(default_factory=dict)
    potcar_sources: dict[str, Path] = field(default_factory=dict)
    initial_seed: MlabParseResult | None = None
    warnings: tuple[PreflightDiagnostic, ...] = ()
    trusted_sc_rlx: bool | None = None
    trusted_sc: tuple[int, int] | None = None
    provenance_kind: str | None = None
    provenance_evidence: dict[str, object] = field(default_factory=dict)


def preflight_stage0(config: DPmoireLiteConfig) -> BuildPreflightResult:
    diagnostics: list[PreflightDiagnostic] = []
    warning_diagnostics: list[PreflightDiagnostic] = []
    templates = _validate_templates(
        _stage0_templates(config),
        config,
        diagnostics,
        warning_diagnostics,
    )
    _validate_submit_script(config, diagnostics)

    structures = _read_structures(config, diagnostics)
    rcut = None
    potcar_sources: dict[str, Path] = {}
    if structures is not None:
        try:
            rcut = _resolve_rcut(config, structures)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="structure",
                    path=config.input_dir,
                    reason=str(exc),
                )
            )

        elements = get_ordered_elements(structures.new_struct)
        potcar_sources = _validate_potcars(config, elements, diagnostics)

    stackings = tuple(generate_stackings(config.n_sectors))
    _raise_if_failed(diagnostics)
    _emit_warnings(warning_diagnostics)
    return BuildPreflightResult(
        stage=config.stage,
        structures=structures,
        stackings=stackings,
        rcut=rcut,
        templates=templates,
        potcar_sources=potcar_sources,
        warnings=tuple(warning_diagnostics),
    )


def preflight_stage1(
    config: DPmoireLiteConfig,
    stackings: list[tuple[int, int]],
) -> BuildPreflightResult:
    diagnostics: list[PreflightDiagnostic] = []
    warning_diagnostics: list[PreflightDiagnostic] = []
    templates = _validate_templates(
        _stage1_templates(config),
        config,
        diagnostics,
        warning_diagnostics,
    )
    _validate_submit_script(config, diagnostics)

    structures = _read_structures(config, diagnostics)
    rcut = None
    potcar_sources: dict[str, Path] = {}
    if structures is not None:
        try:
            rcut = _resolve_rcut(config, structures)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="structure",
                    path=config.input_dir,
                    reason=str(exc),
                )
            )
        elements = get_ordered_elements(structures.new_struct)
        potcar_sources = _validate_potcars(config, elements, diagnostics)

    for stacking in stackings:
        _validate_relaxation(config.work_dir / "rlx" / f"{stacking[0]}_{stacking[1]}", diagnostics)

    trusted_sc_rlx = None
    trusted_sc = None
    provenance_kind = None
    provenance_evidence: dict[str, object] = {}
    manifest_result = read_manifest(config.work_dir, "rlx")
    if manifest_result.kind == "legacy":
        trusted_sc_rlx, trusted_sc = _infer_legacy_stage1_provenance(
            config,
            stackings,
            structures,
            diagnostics,
        )
        if trusted_sc_rlx is not None:
            provenance_kind = "legacy_inference"
            provenance_evidence = {
                "manifest": "rlx/manifest.yaml",
                "method": "atom_count_composition_cell",
                "stackings": [[i, j] for i, j in stackings],
            }
    elif manifest_result.kind == "current" and manifest_result.manifest is not None:
        provenance = manifest_result.manifest.structure_provenance
        if provenance:
            trusted_sc_rlx, trusted_sc = _validate_strict_stage1_provenance(
                config,
                manifest_result.manifest,
                stackings,
                structures,
                diagnostics,
            )
            if trusted_sc_rlx is not None:
                provenance_kind = "strict"
                provenance_evidence = {
                    "manifest": "rlx/manifest.yaml",
                    "schema": provenance.get("schema"),
                    "stackings": [[i, j] for i, j in stackings],
                }

    if config.preserve_grid_shift_md:
        if manifest_result.kind == "legacy":
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path("rlx") / "manifest.yaml",
                    reason=(
                        "preserve_grid_shift_md requires strict Stage0 provenance; "
                        "legacy relaxation manifests cannot preserve grid-shift anchors"
                    ),
                )
            )
        elif manifest_result.kind == "current" and manifest_result.manifest is not None:
            provenance = manifest_result.manifest.structure_provenance
            if not provenance:
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="provenance",
                        path=Path("rlx") / "manifest.yaml",
                        reason=(
                            "preserve_grid_shift_md requires strict Stage0 provenance "
                            "with grid_shift_anchors"
                        ),
                    )
                )
            else:
                _validate_preserved_grid_shift_anchors(
                    config,
                    manifest_result.manifest,
                    stackings,
                    diagnostics,
                )
        else:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path("rlx") / "manifest.yaml",
                    reason=(
                        "preserve_grid_shift_md requires a current strict Stage0 "
                        "relaxation manifest with grid_shift_anchors"
                    ),
                )
            )

    initial_seed = None
    if config.vasp_ml:
        initial_seed = _validate_initial_seed(config, diagnostics)

    _raise_if_failed(diagnostics)
    _emit_warnings(warning_diagnostics)
    return BuildPreflightResult(
        stage=config.stage,
        structures=structures,
        stackings=tuple(stackings),
        rcut=rcut,
        templates=templates,
        potcar_sources=potcar_sources,
        initial_seed=initial_seed,
        warnings=tuple(warning_diagnostics),
        trusted_sc_rlx=trusted_sc_rlx,
        trusted_sc=trusted_sc,
        provenance_kind=provenance_kind,
        provenance_evidence=provenance_evidence,
    )


def _stage0_templates(config: DPmoireLiteConfig) -> list[tuple[str, Path]]:
    templates = []
    if config.init_mlff:
        templates.append(("init_INCAR", config.input_dir / "init_INCAR"))
    if config.do_relaxation:
        templates.append(("rlx_INCAR", config.input_dir / "rlx_INCAR"))
    if config.twist_val:
        templates.append(("val_INCAR", config.input_dir / "val_INCAR"))
    return templates


def _stage1_templates(config: DPmoireLiteConfig) -> list[tuple[str, Path]]:
    templates = [("MD_INCAR", config.input_dir / "MD_INCAR")]
    if config.include_monolayer_md:
        templates.append(("MD_monolayer_INCAR", config.input_dir / "MD_monolayer_INCAR"))
    return templates


STRICT_STRUCTURE_PROVENANCE_SCHEMA = "dpmoire-lite.structure-provenance.v1"


def _normalized_d_reference(config: DPmoireLiteConfig) -> dict[str, object] | None:
    if config.d_reference is None:
        return None
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in config.d_reference.items()
    }


def _canonical_provenance_value(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            sorted(
                (str(key), _canonical_provenance_value(item))
                for key, item in value.items()
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_provenance_value(item) for item in value)
    return value


def _provenance_values_equal(left: object, right: object) -> bool:
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return _canonical_provenance_value(left) == _canonical_provenance_value(right)


def _coerce_provenance_pair(
    value: object,
    field: str,
    path: Path,
    diagnostics: list[PreflightDiagnostic],
) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=path,
                reason=f"{field} must contain exactly two integer values",
            )
        )
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=path,
                reason=f"{field} must contain exactly two integer values",
            )
        )
        return None
    return int(value[0]), int(value[1])


def _identity_from_record(
    record: object,
    path: Path,
    diagnostics: list[PreflightDiagnostic],
) -> StructureIdentity | None:
    if not isinstance(record, Mapping):
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=path,
                reason="structure identity record must be a mapping",
            )
        )
        return None
    try:
        sha256 = record["sha256"]
        atom_count = record["atom_count"]
        ordered_elements = record["ordered_elements"]
        composition = record["composition"]
        cell = record["cell"]
        if not isinstance(sha256, str):
            raise TypeError("sha256 must be a string")
        if isinstance(atom_count, bool) or not isinstance(atom_count, int):
            raise TypeError("atom_count must be an integer")
        if not isinstance(ordered_elements, (list, tuple)):
            raise TypeError("ordered_elements must be a list")
        if not isinstance(composition, Mapping):
            raise TypeError("composition must be a mapping")
        if not isinstance(cell, (list, tuple)) or len(cell) != 3:
            raise TypeError("cell must be a 3x3 matrix")
        parsed_cell = []
        for row in cell:
            if not isinstance(row, (list, tuple)) or len(row) != 3:
                raise TypeError("cell must be a 3x3 matrix")
            parsed_cell.append(tuple(float(value) for value in row))
        parsed_composition = []
        for symbol, count in composition.items():
            if isinstance(count, bool) or not isinstance(count, int):
                raise TypeError("composition counts must be integers")
            parsed_composition.append((str(symbol), int(count)))
        return StructureIdentity(
            sha256=sha256,
            atom_count=atom_count,
            ordered_elements=tuple(str(value) for value in ordered_elements),
            composition=tuple(parsed_composition),
            cell=tuple(parsed_cell),
        )
    except (KeyError, TypeError, ValueError) as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=path,
                reason=f"invalid structure identity record: {exc}",
            )
        )
        return None


def _append_identity_mismatch(
    expected: StructureIdentity,
    actual: StructureIdentity,
    path: Path,
    diagnostics: list[PreflightDiagnostic],
    label: str,
) -> None:
    differences = structure_identity_differences(expected, actual)
    if differences:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=path,
                reason=f"{label} identity mismatch: {', '.join(differences)}",
            )
        )


def _validate_strict_stage1_provenance(
    config: DPmoireLiteConfig,
    manifest: Manifest,
    stackings: list[tuple[int, int]],
    structures: StructureHandler | None,
    diagnostics: list[PreflightDiagnostic],
) -> tuple[bool | None, tuple[int, int] | None]:
    start_diagnostics = len(diagnostics)
    manifest_path = Path("rlx") / "manifest.yaml"
    provenance = manifest.structure_provenance

    if provenance.get("schema") != STRICT_STRUCTURE_PROVENANCE_SCHEMA:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason=(
                    "unsupported structure provenance schema; expected "
                    f"{STRICT_STRUCTURE_PROVENANCE_SCHEMA}"
                ),
            )
        )
    if type(provenance.get("stage")) is not int or provenance.get("stage") != 0:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason="structure provenance stage must be 0",
            )
        )

    manifest_stackings = [[int(i), int(j)] for i, j in stackings]
    if provenance.get("stackings") != manifest_stackings:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason=(
                    "stackings differ between relaxation manifest and structure "
                    f"provenance: manifest={manifest_stackings!r}, "
                    f"provenance={provenance.get('stackings')!r}"
                ),
            )
        )
    expected_directories = [f"rlx/{i}_{j}" for i, j in stackings]
    if manifest.directories != expected_directories:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason=(
                    "relaxation manifest directories do not match its stacking list: "
                    f"expected={expected_directories!r}, actual={manifest.directories!r}"
                ),
            )
        )

    manifest_sc_rlx = provenance.get("sc_rlx")
    if not isinstance(manifest_sc_rlx, bool):
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason="sc_rlx must be a boolean in structure provenance",
            )
        )
    elif config.sc_rlx != manifest_sc_rlx:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason=(
                    "sc_rlx conflicts with Stage0 provenance: "
                    f"manifest={manifest_sc_rlx!r}, current config={config.sc_rlx!r}"
                ),
            )
        )

    manifest_sc = _coerce_provenance_pair(provenance.get("sc"), "sc", manifest_path, diagnostics)
    manifest_n_sectors = _coerce_provenance_pair(
        provenance.get("n_sectors"), "n_sectors", manifest_path, diagnostics
    )
    if manifest_sc_rlx is True and manifest_sc is not None and tuple(config.sc) != manifest_sc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason=(
                    "sc conflicts with a supercell relaxation: "
                    f"manifest={list(manifest_sc)!r}, current config={list(config.sc)!r}"
                ),
            )
        )
    if manifest_n_sectors is not None and tuple(config.n_sectors) != manifest_n_sectors:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason=(
                    "n_sectors differs from Stage0 provenance: "
                    f"manifest={list(manifest_n_sectors)!r}, "
                    f"current config={list(config.n_sectors)!r}"
                ),
            )
        )

    immutable_fields = {
        "symm_reduce": config.symm_reduce,
        "d": config.d,
        "d_mode": config.d_mode,
        "d_reference": _normalized_d_reference(config),
    }
    for field_name, current_value in immutable_fields.items():
        if field_name not in provenance:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=manifest_path,
                    reason=f"missing immutable field {field_name}",
                )
            )
        elif not _provenance_values_equal(provenance[field_name], current_value):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=manifest_path,
                    reason=(
                        f"{field_name} differs from Stage0 provenance: "
                        f"manifest={provenance[field_name]!r}, current config={current_value!r}"
                    ),
                )
            )
    if isinstance(manifest_sc_rlx, bool):
        expected_semantics = (
            "stage0_relaxation_supercell"
            if manifest_sc_rlx
            else "stage0_relaxation_primitive"
        )
        if provenance.get("sc_semantics") != expected_semantics:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=manifest_path,
                    reason=(
                        "sc_semantics is inconsistent with manifest sc_rlx: "
                        f"expected={expected_semantics!r}, "
                        f"actual={provenance.get('sc_semantics')!r}"
                    ),
                )
            )

    expected_inputs = {
        "input/top_layer.poscar",
        "input/bot_layer.poscar",
    }
    input_records = provenance.get("inputs")
    if not isinstance(input_records, Mapping):
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason="inputs must be a mapping containing both Stage0 layer POSCARs",
            )
        )
    else:
        for relative_path in sorted(expected_inputs - set(input_records)):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path(relative_path),
                    reason="missing input identity record",
                )
            )
        for relative_path in sorted(set(input_records) - expected_inputs):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path(relative_path),
                    reason="unexpected input identity record",
                )
            )
        if structures is not None and structures.top_atoms is not None and structures.bot_atoms is not None:
            input_sources = {
                "input/top_layer.poscar": (
                    config.input_dir / "top_layer.poscar",
                    structures.top_atoms,
                ),
                "input/bot_layer.poscar": (
                    config.input_dir / "bot_layer.poscar",
                    structures.bot_atoms,
                ),
            }
            for relative_path, (source_path, atoms) in input_sources.items():
                record = input_records.get(relative_path)
                if not isinstance(record, Mapping):
                    diagnostics.append(
                        PreflightDiagnostic(
                            domain="provenance",
                            path=Path(relative_path),
                            reason="missing or invalid input identity record",
                        )
                    )
                    continue
                if record.get("path") != relative_path:
                    diagnostics.append(
                        PreflightDiagnostic(
                            domain="provenance",
                            path=Path(relative_path),
                            reason=f"identity record path is {record.get('path')!r}",
                        )
                    )
                expected_identity = _identity_from_record(record, Path(relative_path), diagnostics)
                try:
                    actual_identity = structure_identity(source_path, atoms)
                except Exception as exc:
                    diagnostics.append(
                        PreflightDiagnostic(
                            domain="provenance",
                            path=Path(relative_path),
                            reason=f"could not read current input identity: {exc}",
                        )
                    )
                else:
                    if expected_identity is not None:
                        _append_identity_mismatch(
                            expected_identity,
                            actual_identity,
                            Path(relative_path),
                            diagnostics,
                            "input",
                        )

    expected_rlx_paths = {f"rlx/{i}_{j}/POSCAR" for i, j in stackings}
    rlx_records = provenance.get("rlx_poscars")
    if not isinstance(rlx_records, Mapping):
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason="rlx_poscars must be a mapping of every generated POSCAR",
            )
        )
        rlx_records = {}
    else:
        for relative_path in sorted(expected_rlx_paths - set(rlx_records)):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path(relative_path),
                    reason="missing generated POSCAR identity record",
                )
            )
        for relative_path in sorted(set(rlx_records) - expected_rlx_paths):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path(relative_path),
                    reason="unexpected generated POSCAR identity record",
                )
            )

    stage0_poscar_identities: dict[str, StructureIdentity] = {}
    for i, j in stackings:
        relative_path = f"rlx/{i}_{j}/POSCAR"
        display_path = Path(relative_path)
        record = rlx_records.get(relative_path)
        if not isinstance(record, Mapping):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path,
                    reason="missing or invalid generated POSCAR identity record",
                )
            )
        else:
            if record.get("path") != relative_path:
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="provenance",
                        path=display_path,
                        reason=f"identity record path is {record.get('path')!r}",
                    )
                )
            expected_identity = _identity_from_record(record, display_path, diagnostics)
            poscar = config.work_dir / relative_path
            try:
                actual_identity = structure_identity(poscar)
            except Exception as exc:
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="provenance",
                        path=display_path,
                        reason=f"could not read generated POSCAR identity: {exc}",
                    )
                )
            else:
                stage0_poscar_identities[relative_path] = actual_identity
                if expected_identity is not None:
                    _append_identity_mismatch(
                        expected_identity,
                        actual_identity,
                        display_path,
                        diagnostics,
                        "generated POSCAR",
                    )

        contcar = config.work_dir / f"rlx/{i}_{j}/CONTCAR"
        if relative_path not in stage0_poscar_identities or not contcar.is_file():
            continue
        try:
            contcar_identity = structure_identity(contcar)
        except Exception:
            continue
        topology_differences = tuple(
            difference
            for difference in structure_identity_differences(
                stage0_poscar_identities[relative_path], contcar_identity
            )
            if difference in {"atom_count", "ordered_elements", "composition"}
        )
        if topology_differences:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path(f"rlx/{i}_{j}/CONTCAR"),
                    reason=(
                        "CONTCAR topology/composition mismatch: "
                        f"{', '.join(topology_differences)}"
                    ),
                )
            )

    if len(diagnostics) != start_diagnostics:
        return None, None
    if not isinstance(manifest_sc_rlx, bool) or manifest_sc is None:
        return None, None
    trusted_sc = tuple(config.sc) if not manifest_sc_rlx else manifest_sc
    return manifest_sc_rlx, trusted_sc


def _preserved_anchor_constraints(
    record: Mapping,
    path: Path,
    diagnostics: list[PreflightDiagnostic],
) -> dict[int, list[bool]] | None:
    top_index = record.get("top_index")
    bottom_index = record.get("bottom_index")
    if (
        any(
            isinstance(index, bool) or not isinstance(index, int)
            for index in (top_index, bottom_index)
        )
        or top_index == bottom_index
    ):
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=path,
                reason="grid-shift anchor record must contain two distinct integer indices",
            )
        )
        return None

    fixed_masks = record.get("fixed_masks")
    if not isinstance(fixed_masks, Mapping):
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=path,
                reason="grid-shift anchor fixed_masks must be a mapping",
            )
        )
        return None

    normalized_masks: dict[int, object] = {}
    for raw_index, mask in fixed_masks.items():
        if isinstance(raw_index, bool):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=path,
                    reason="grid-shift anchor fixed_masks indices must be integers",
                )
            )
            continue
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=path,
                    reason="grid-shift anchor fixed_masks indices must be integers",
                )
            )
            continue
        if index in normalized_masks:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=path,
                    reason="grid-shift anchor fixed_masks contain duplicate indices",
                )
            )
        normalized_masks[index] = mask

    expected_indices = {top_index, bottom_index}
    if set(normalized_masks) != expected_indices:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=path,
                reason=(
                    "grid-shift anchor fixed_masks must contain exactly the top and "
                    f"bottom indices: expected={sorted(expected_indices)!r}, "
                    f"actual={sorted(normalized_masks)!r}"
                ),
            )
        )
        return None

    expected: dict[int, list[bool]] = {}
    for index in (top_index, bottom_index):
        mask = normalized_masks[index]
        if (
            not isinstance(mask, (list, tuple))
            or len(mask) != 3
            or any(type(value) is not bool for value in mask)
            or list(mask) != [True, True, False]
        ):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=path,
                    reason="grid-shift anchor fixed mask must be [true, true, false]",
                )
            )
            return None
        expected[index] = list(mask)
    return expected


def _source_constraint_set(
    atoms,
    path: Path,
    diagnostics: list[PreflightDiagnostic],
) -> dict[int, list[bool]]:
    constraints: dict[int, list[bool]] = {}
    for constraint in atoms.constraints:
        if not isinstance(constraint, FixScaled):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=path,
                    reason=(
                        "source CONTCAR uses an unsupported constraint type; "
                        "preserved anchors must use FixScaled"
                    ),
                )
            )
            continue
        indexes = np.asarray(getattr(constraint, "index", []), dtype=int).reshape(-1)
        mask = np.asarray(getattr(constraint, "mask", []), dtype=bool).reshape(-1)
        if indexes.size != 1 or mask.shape != (3,):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=path,
                    reason=(
                        "source CONTCAR constraints must contain one atom and a "
                        "three-component mask"
                    ),
                )
            )
            continue
        index = int(indexes[0])
        if index < 0 or index >= len(atoms) or index in constraints:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=path,
                    reason="source CONTCAR constraints contain invalid or duplicate anchor indices",
                )
            )
            continue
        constraints[index] = mask.tolist()
    return constraints


def _validate_preserved_grid_shift_anchors(
    config: DPmoireLiteConfig,
    manifest: Manifest,
    stackings: list[tuple[int, int]],
    diagnostics: list[PreflightDiagnostic],
) -> None:
    manifest_path = Path("rlx") / "manifest.yaml"
    provenance = manifest.structure_provenance
    anchors = provenance.get("grid_shift_anchors")
    if not isinstance(anchors, Mapping):
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason="preserve_grid_shift_md requires grid_shift_anchors mapping",
            )
        )
        return

    for i, j in stackings:
        relative_directory = f"rlx/{i}_{j}"
        display_path = Path(relative_directory)
        anchor_record = anchors.get(relative_directory)
        if not isinstance(anchor_record, Mapping):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path,
                    reason="missing or invalid grid-shift anchor record",
                )
            )
            continue

        expected_constraints = _preserved_anchor_constraints(
            anchor_record,
            display_path,
            diagnostics,
        )
        poscar = config.work_dir / relative_directory / "POSCAR"
        contcar = config.work_dir / relative_directory / "CONTCAR"
        try:
            poscar_atoms = read_vasp(poscar)
            poscar_identity = structure_identity(poscar, poscar_atoms)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path / "POSCAR",
                    reason=f"could not validate preserved-anchor POSCAR: {exc}",
                )
            )
            continue

        expected_atom_count = anchor_record.get("atom_count")
        expected_hash = anchor_record.get("poscar_sha256")
        if (
            isinstance(expected_atom_count, bool)
            or not isinstance(expected_atom_count, int)
            or expected_atom_count != poscar_identity.atom_count
        ):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path / "POSCAR",
                    reason=(
                        "preserved grid-shift anchor atom_count does not match POSCAR: "
                        f"anchor={expected_atom_count!r}, actual={poscar_identity.atom_count!r}"
                    ),
                )
            )
        if not isinstance(expected_hash, str) or expected_hash != poscar_identity.sha256:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path / "POSCAR",
                    reason=(
                        "preserved grid-shift anchor POSCAR hash does not match "
                        f"the current POSCAR: anchor={expected_hash!r}, "
                        f"actual={poscar_identity.sha256!r}"
                    ),
                )
            )

        try:
            contcar_atoms = read_vasp(contcar)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path / "CONTCAR",
                    reason=f"could not validate preserved grid-shift anchors: {exc}",
                )
            )
            continue

        if len(poscar_atoms) != len(contcar_atoms):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path / "CONTCAR",
                    reason=(
                        "preserved grid-shift anchor atom order/count mismatch: "
                        f"POSCAR={len(poscar_atoms)}, CONTCAR={len(contcar_atoms)}"
                    ),
                )
            )
        elif poscar_atoms.get_chemical_symbols() != contcar_atoms.get_chemical_symbols():
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path / "CONTCAR",
                    reason="preserved grid-shift anchor atom order mismatch",
                )
            )

        actual_constraints = _source_constraint_set(
            contcar_atoms,
            display_path / "CONTCAR",
            diagnostics,
        )
        if expected_constraints is not None and actual_constraints != expected_constraints:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=display_path / "CONTCAR",
                    reason=(
                        "source CONTCAR constraint set does not exactly match "
                        "the two manifest grid-shift anchors: "
                        f"expected={expected_constraints!r}, actual={actual_constraints!r}"
                    ),
                )
            )


def _legacy_composition(atoms) -> Counter[str]:
    return Counter(atoms.get_chemical_symbols())


def _infer_legacy_structure_source(primitive, candidate) -> tuple[bool, tuple[int, int] | None]:
    primitive_count = len(primitive)
    candidate_count = len(candidate)
    if primitive_count <= 0:
        raise ValueError("primitive bilayer has no atoms")

    primitive_composition = _legacy_composition(primitive)
    candidate_composition = _legacy_composition(candidate)
    if candidate_count == primitive_count and candidate_composition == primitive_composition:
        count_sc_rlx = False
        multiplier = 1
    else:
        if candidate_count % primitive_count:
            raise ValueError("atom count is not an integer multiple of the primitive bilayer")
        multiplier = candidate_count // primitive_count
        expected_composition = Counter(
            {
                symbol: count * multiplier
                for symbol, count in primitive_composition.items()
            }
        )
        if candidate_composition != expected_composition:
            raise ValueError("atom count and composition do not agree on a supercell multiplier")
        if multiplier <= 1:
            raise ValueError("atom count and composition do not identify a supercell")
        count_sc_rlx = True

    primitive_cell = np.asarray(primitive.cell.array[:2, :2], dtype=float)
    candidate_cell = np.asarray(candidate.cell.array[:2, :2], dtype=float)
    cell_matches_primitive = np.allclose(
        primitive_cell,
        candidate_cell,
        atol=CELL_ATOL,
        rtol=CELL_RTOL,
    )
    if not count_sc_rlx:
        if not cell_matches_primitive:
            raise ValueError(
                "atom count/composition identify a primitive structure but its "
                "in-plane cell does not match the primitive bilayer"
            )
        return False, None

    if cell_matches_primitive:
        raise ValueError(
            "atom count/composition identify a supercell but its in-plane cell "
            "matches the primitive bilayer"
        )
    try:
        relation = inplane_supercell_relation(primitive, candidate)
    except ValueError as exc:
        raise ValueError(f"supercell count/composition conflicts with its in-plane cell: {exc}") from exc
    matrix = relation.matrix
    if matrix[0][1] != 0 or matrix[1][0] != 0 or matrix[0][0] <= 0 or matrix[1][1] <= 0:
        raise ValueError(
            "in-plane cell transform is not a supported positive diagonal supercell"
        )
    if relation.determinant != multiplier:
        raise ValueError(
            "supercell count/composition multiplier conflicts with in-plane cell determinant"
        )
    return True, (matrix[0][0], matrix[1][1])


def _legacy_topology_differences(expected, actual) -> tuple[str, ...]:
    differences = []
    if len(expected) != len(actual):
        differences.append("atom_count")
    if _legacy_composition(expected) != _legacy_composition(actual):
        differences.append("composition")
    return tuple(differences)


def _infer_legacy_stage1_provenance(
    config: DPmoireLiteConfig,
    stackings: list[tuple[int, int]],
    structures: StructureHandler | None,
    diagnostics: list[PreflightDiagnostic],
) -> tuple[bool | None, tuple[int, int] | None]:
    start_diagnostics = len(diagnostics)
    if structures is None or structures.new_struct is None:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=Path("rlx") / "manifest.yaml",
                reason="legacy inference requires readable current input structures",
            )
        )
        return None, None

    primitive = structures.new_struct
    conclusions: list[tuple[bool, tuple[int, int] | None]] = []
    for i, j in stackings:
        directory = Path("rlx") / f"{i}_{j}"
        poscar = config.work_dir / directory / "POSCAR"
        contcar = config.work_dir / directory / "CONTCAR"
        if not poscar.is_file():
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=directory / "POSCAR",
                    reason="legacy inference requires the Stage0 POSCAR",
                )
            )
            continue
        try:
            candidate = read_vasp(poscar)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=directory / "POSCAR",
                    reason=f"legacy inference could not read POSCAR: {exc}",
                )
            )
            continue
        try:
            conclusion = _infer_legacy_structure_source(primitive, candidate)
        except ValueError as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=directory / "POSCAR",
                    reason=f"legacy inference failed: {exc}",
                )
            )
            continue

        if not contcar.is_file():
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=directory / "CONTCAR",
                    reason="legacy inference requires the converged CONTCAR",
                )
            )
            continue
        try:
            contcar_atoms = read_vasp(contcar)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=directory / "CONTCAR",
                    reason=f"legacy inference could not read CONTCAR: {exc}",
                )
            )
            continue
        topology_differences = _legacy_topology_differences(candidate, contcar_atoms)
        if topology_differences:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=directory / "CONTCAR",
                    reason=(
                        "legacy inference CONTCAR topology/composition mismatch: "
                        f"{', '.join(topology_differences)}"
                    ),
                )
            )
            continue
        conclusions.append(conclusion)

    if conclusions:
        inferred_modes = {mode for mode, _ in conclusions}
        inferred_supercells = {sc for mode, sc in conclusions if mode and sc is not None}
        if len(inferred_modes) != 1:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path("rlx") / "manifest.yaml",
                    reason="legacy inference produced inconsistent sc_rlx conclusions across stackings",
                )
            )
        elif True in inferred_modes and len(inferred_supercells) != 1:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="provenance",
                    path=Path("rlx") / "manifest.yaml",
                    reason="legacy inference produced inconsistent supercell directions across stackings",
                )
            )

    if len(diagnostics) != start_diagnostics:
        return None, None
    if not conclusions:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=Path("rlx") / "manifest.yaml",
                reason="legacy inference found no usable stacking evidence",
            )
        )
        return None, None

    inferred_sc_rlx = conclusions[0][0]
    inferred_sc = conclusions[0][1]
    if config.sc_rlx != inferred_sc_rlx:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=Path("rlx") / "manifest.yaml",
                reason=(
                    "Legacy inference conflicts with current config: "
                    f"inferred sc_rlx={inferred_sc_rlx!r}, current sc_rlx={config.sc_rlx!r}"
                ),
            )
        )
    elif inferred_sc_rlx and inferred_sc is not None and tuple(config.sc) != inferred_sc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=Path("rlx") / "manifest.yaml",
                reason=(
                    "Legacy inference conflicts with current config: "
                    f"inferred sc={list(inferred_sc)!r}, current sc={list(config.sc)!r}"
                ),
            )
        )
    if len(diagnostics) != start_diagnostics:
        return None, None
    return inferred_sc_rlx, tuple(config.sc) if not inferred_sc_rlx else inferred_sc


def _validate_templates(
    templates: list[tuple[str, Path]],
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
    warning_diagnostics: list[PreflightDiagnostic],
) -> dict[str, IncarAnalysis]:
    del config
    analyses: dict[str, IncarAnalysis] = {}
    for name, path in templates:
        if not path.is_file():
            diagnostics.append(
                PreflightDiagnostic(
                    domain="template",
                    path=path,
                    reason=f"missing required template {name}",
                )
            )
            continue

        try:
            analysis = parse_incar(
                path.read_text(encoding="utf-8"),
                source_name=str(path),
            ).analyze()
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="incar",
                    path=path,
                    reason=str(exc),
                )
            )
            continue

        analyses[name] = analysis
        for duplicate in analysis.blocking_conflicts:
            values = ", ".join(
                f"line {assignment.location.line}: {assignment.value}"
                for assignment in duplicate.assignments
            )
            diagnostics.append(
                PreflightDiagnostic(
                    domain="incar",
                    path=path,
                    tag=duplicate.tag,
                    reason=f"conflicting definitions: {values}",
                )
            )

        for duplicate in analysis.repairable_warnings:
            warning_diagnostics.append(
                PreflightDiagnostic(
                    domain="incar",
                    path=path,
                    tag=duplicate.tag,
                    reason="repairable duplicate will be normalized during generation",
                )
            )

        luse_vdw_conflict = any(
            duplicate.tag == "LUSE_VDW" for duplicate in analysis.blocking_conflicts
        )
        if not luse_vdw_conflict and analysis.is_effectively_true("LUSE_VDW"):
            kernel = path.parent / "vdw_kernel.bindat"
            if not kernel.is_file():
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="vdw",
                        path=kernel,
                        reason=f"required by {path}",
                    )
                )

    return analyses


def _validate_submit_script(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> None:
    path = config.script_dir / config.dft_script
    if not path.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=path,
                reason="required submit script is missing",
            )
        )


def _read_structures(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> StructureHandler | None:
    reader = object.__new__(StructureHandler)
    input_atoms = {}
    for name in ("top_layer", "bot_layer"):
        path = config.input_dir / f"{name}.poscar"
        try:
            input_atoms[name] = reader.read_atoms(path)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="structure",
                    path=path,
                    reason=str(exc),
                )
            )

    if len(input_atoms) != 2:
        return None

    for name, atoms in input_atoms.items():
        try:
            validate_cartesian_z_slab_cell(atoms, f"{name}.poscar")
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="structure",
                    path=config.input_dir / f"{name}.poscar",
                    reason=str(exc),
                )
            )

    try:
        return StructureHandler(
            config.input_dir,
            config.work_dir,
            config.n_sectors,
            config.d,
            config.d_mode,
            config.d_reference,
        )
    except Exception as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="structure",
                path=config.input_dir,
                reason=str(exc),
            )
        )
        return None


def _resolve_rcut(config: DPmoireLiteConfig, structures: StructureHandler) -> float:
    if config.r_cut >= 0:
        return config.r_cut
    if structures.top_atoms is None or structures.bot_atoms is None:
        raise ValueError("Input layer cells are required to resolve default r_cut")
    max_layer_a = max(
        float(np.linalg.norm(atoms.cell.array[index]))
        for atoms in (structures.top_atoms, structures.bot_atoms)
        for index in (0, 1)
    )
    return float(np.sqrt(max_layer_a**2 + config.d**2) * 1.1)


def _validate_potcars(
    config: DPmoireLiteConfig,
    elements: list[str],
    diagnostics: list[PreflightDiagnostic],
) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for element in elements:
        try:
            source_dir = resolve_potcar_dir(
                element,
                config.potcar_dir,
                potcar_policy=config.potcar_policy,
            )
            read_enmax(source_dir / "POTCAR")
            sources[element] = source_dir
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="potcar",
                    path=config.potcar_dir / element / "POTCAR",
                    reason=str(exc),
                )
            )
    return sources


def _validate_relaxation(
    directory: Path,
    diagnostics: list[PreflightDiagnostic],
) -> None:
    display_directory = Path("rlx") / directory.name
    outcar = directory / "OUTCAR"
    if not outcar.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="relaxation",
                path=display_directory,
                reason="Missing OUTCAR",
            )
        )
    else:
        try:
            text = outcar.read_text(encoding="utf-8", errors="ignore")
            if VASP_RELAXATION_CONVERGED_PHRASE not in text:
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="relaxation",
                        path=display_directory,
                        reason="Relaxation did not converge",
                    )
                )
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="relaxation",
                    path=display_directory,
                    reason=str(exc),
                )
            )

    contcar = directory / "CONTCAR"
    if not contcar.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="relaxation",
                path=display_directory,
                reason="Missing CONTCAR",
            )
        )
    else:
        try:
            read_vasp(contcar)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="relaxation",
                    path=display_directory,
                    reason=f"CONTCAR is not readable by ASE: {exc}",
                )
            )


def _validate_initial_seed(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> MlabParseResult | None:
    init_dir = config.work_dir / "init_mlff"
    seed_path = init_dir / "ML_ABN"
    force_field_path = init_dir / "ML_FFN"
    seed_display_path = Path("init_mlff") / "ML_ABN"
    force_field_display_path = Path("init_mlff") / "ML_FFN"
    if not seed_path.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason="missing required MLFF file",
            )
        )
    elif seed_path.stat().st_size == 0:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason="ML_ABN seed is empty",
            )
        )

    if not force_field_path.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=force_field_display_path,
                reason="missing required MLFF file",
            )
        )
    elif force_field_path.stat().st_size == 0:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=force_field_display_path,
                reason="ML_FFN file is empty",
            )
        )

    if not seed_path.is_file() or seed_path.stat().st_size == 0:
        return None
    try:
        result = parse_mlab(seed_path)
    except Exception as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason=str(exc),
            )
        )
        return None
    if result.status != "complete" or result.declared_count != result.complete_count:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason=(
                    "initial ML_ABN seed must be complete: "
                    f"status={result.status!r}, declared={result.declared_count}, "
                    f"complete={result.complete_count}"
                ),
            )
        )
        return None
    return result


def _raise_if_failed(diagnostics: list[PreflightDiagnostic]) -> None:
    if not diagnostics:
        return
    details = "\n".join(f"- {diagnostic.format()}" for diagnostic in diagnostics)
    raise RuntimeError(
        f"Build preflight failed:\n{details}\nNo calculation directories were generated."
    )


def _emit_warnings(diagnostics: list[PreflightDiagnostic]) -> None:
    for diagnostic in diagnostics:
        pywarnings.warn(diagnostic.format(), UserWarning, stacklevel=3)
