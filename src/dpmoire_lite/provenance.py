from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from ase import Atoms
from ase.constraints import FixScaled
from ase.io.vasp import read_vasp

from .config import DPmoireLiteConfig
from .manifest import Manifest, ManifestReadResult
from .structures import StructureHandler


CELL_ATOL = 1e-8
CELL_RTOL = 1e-8
TRANSFORM_ATOL = 1e-8


@dataclass(frozen=True)
class StructureIdentity:
    sha256: str
    atom_count: int
    ordered_elements: tuple[str, ...]
    composition: tuple[tuple[str, int], ...]
    cell: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class InplaneSupercellRelation:
    matrix: tuple[tuple[int, int], tuple[int, int]]
    determinant: int
    atom_multiplier: int


@dataclass(frozen=True)
class ProvenanceIssue:
    severity: Literal["error", "warning"]
    path: Path
    reason: str


@dataclass(frozen=True)
class Stage1ProvenanceResult:
    kind: Literal["strict", "legacy_inference"] | None
    trusted_sc_rlx: bool | None
    trusted_sc: tuple[int, int] | None
    evidence: tuple[tuple[str, object], ...] = ()
    validated_anchor_indices: tuple[tuple[int, int], ...] = ()
    issues: tuple[ProvenanceIssue, ...] = ()


def structure_identity(path: Path, atoms: Atoms | None = None) -> StructureIdentity:
    path = Path(path)
    if atoms is None:
        atoms = read_vasp(path)

    ordered_elements = tuple(atoms.get_chemical_symbols())
    counts: dict[str, int] = {}
    for symbol in ordered_elements:
        counts[symbol] = counts.get(symbol, 0) + 1

    cell = tuple(
        tuple(float(value) for value in row)
        for row in np.asarray(atoms.cell.array, dtype=float)
    )
    return StructureIdentity(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        atom_count=len(atoms),
        ordered_elements=ordered_elements,
        composition=tuple(counts.items()),
        cell=cell,
    )


def structure_identity_differences(
    expected: StructureIdentity,
    actual: StructureIdentity,
) -> tuple[str, ...]:
    differences: list[str] = []
    if expected.sha256 != actual.sha256:
        differences.append("sha256")
    if expected.atom_count != actual.atom_count:
        differences.append("atom_count")
    if expected.ordered_elements != actual.ordered_elements:
        differences.append("ordered_elements")
    if expected.composition != actual.composition:
        differences.append("composition")
    if not np.allclose(
        np.asarray(expected.cell),
        np.asarray(actual.cell),
        atol=CELL_ATOL,
        rtol=CELL_RTOL,
    ):
        differences.append("cell")
    return tuple(differences)


def inplane_supercell_relation(
    primitive: Atoms,
    candidate: Atoms,
    *,
    atol: float = TRANSFORM_ATOL,
    rtol: float = CELL_RTOL,
) -> InplaneSupercellRelation:
    primitive_count = len(primitive)
    candidate_count = len(candidate)
    if primitive_count <= 0:
        raise ValueError("Primitive structure must contain at least one atom")
    if candidate_count % primitive_count:
        raise ValueError(
            "Candidate atom count is not an integer multiple of the primitive atom count"
        )

    primitive_cell = np.asarray(primitive.cell.array[:2, :2], dtype=float)
    candidate_cell = np.asarray(candidate.cell.array[:2, :2], dtype=float)
    try:
        transform = candidate_cell @ np.linalg.inv(primitive_cell)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Primitive in-plane cell is singular") from exc

    rounded = np.rint(transform)
    if not np.allclose(transform, rounded, atol=atol, rtol=0.0):
        raise ValueError(
            f"In-plane cell transform must be integer within atol={atol}; got {transform.tolist()}"
        )

    matrix = (
        (int(rounded[0, 0]), int(rounded[0, 1])),
        (int(rounded[1, 0]), int(rounded[1, 1])),
    )
    reconstructed = rounded @ primitive_cell
    if not np.allclose(reconstructed, candidate_cell, atol=atol, rtol=rtol):
        raise ValueError(
            f"In-plane cell does not match its integer transform within atol={atol}"
        )

    determinant = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    if determinant <= 0:
        raise ValueError(f"In-plane cell transform has invalid determinant {determinant}")

    atom_multiplier = candidate_count // primitive_count
    if determinant != atom_multiplier:
        raise ValueError(
            "In-plane cell transform determinant "
            f"{determinant} does not match atom multiplier {atom_multiplier}"
        )

    return InplaneSupercellRelation(
        matrix=matrix,
        determinant=determinant,
        atom_multiplier=atom_multiplier,
    )


infer_inplane_supercell_relation = inplane_supercell_relation


STRICT_STRUCTURE_PROVENANCE_SCHEMA = "dpmoire-lite.structure-provenance.v1"


def _normalized_d_reference(
    config: DPmoireLiteConfig,
) -> dict[str, object] | None:
    if config.d_reference is None:
        return None
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in config.d_reference.items()
    }


def structure_identity_record(
    path: Path,
    relative_path: str,
    atoms: Atoms | None = None,
) -> dict[str, object]:
    identity = structure_identity(path, atoms)
    return {
        "path": relative_path,
        "sha256": identity.sha256,
        "atom_count": identity.atom_count,
        "ordered_elements": list(identity.ordered_elements),
        "composition": dict(identity.composition),
        "cell": [list(row) for row in identity.cell],
    }


def stage0_grid_shift_anchor_record(
    path: Path,
    identity: dict[str, object],
    top_indexes: list[int],
    bot_indexes: list[int],
) -> dict[str, object]:
    atoms = read_vasp(path)
    entries: dict[int, list[bool]] = {}
    for constraint in atoms.constraints:
        indexes = np.asarray(getattr(constraint, "index", []), dtype=int).reshape(-1)
        mask_value = getattr(constraint, "mask", None)
        if indexes.size == 0 or mask_value is None:
            raise RuntimeError(
                f"Stage0 grid-shift constraints in {path} cannot be represented by fixed_masks"
            )
        mask = np.asarray(mask_value, dtype=bool).reshape(-1)
        if mask.shape != (3,) or mask.tolist() != [True, True, False]:
            raise RuntimeError(
                f"Stage0 grid-shift constraints in {path} must use fixed mask [true, true, false]"
            )
        for index in indexes:
            index = int(index)
            if index < 0 or index >= len(atoms) or index in entries:
                raise RuntimeError(
                    f"Stage0 grid-shift constraints in {path} contain invalid or duplicate anchor indices"
                )
            entries[index] = mask.tolist()

    top_anchors = sorted(set(top_indexes).intersection(entries))
    bot_anchors = sorted(set(bot_indexes).intersection(entries))
    if len(entries) != 2 or len(top_anchors) != 1 or len(bot_anchors) != 1:
        raise RuntimeError(
            f"Stage0 grid-shift constraints in {path} must contain exactly one top and one bottom anchor"
        )

    top_index = top_anchors[0]
    bottom_index = bot_anchors[0]
    return {
        "atom_count": int(identity["atom_count"]),
        "poscar_sha256": str(identity["sha256"]),
        "top_index": top_index,
        "bottom_index": bottom_index,
        "fixed_masks": {
            top_index: entries[top_index],
            bottom_index: entries[bottom_index],
        },
    }


def stage0_structure_provenance(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    stackings,
    rlx_poscars: dict[str, dict[str, object]],
) -> dict[str, object]:
    if structures.top_atoms is None or structures.bot_atoms is None:
        raise RuntimeError("Stage0 structure provenance requires loaded top and bottom inputs")

    input_paths = {
        "input/top_layer.poscar": (config.input_dir / "top_layer.poscar", structures.top_atoms),
        "input/bot_layer.poscar": (config.input_dir / "bot_layer.poscar", structures.bot_atoms),
    }
    inputs = {
        relative_path: structure_identity_record(path, relative_path, atoms)
        for relative_path, (path, atoms) in input_paths.items()
    }
    stacking_values = [[int(i), int(j)] for i, j in stackings]
    return {
        "schema": STRICT_STRUCTURE_PROVENANCE_SCHEMA,
        "stage": 0,
        "sc_rlx": bool(config.sc_rlx),
        "sc": list(config.sc),
        "sc_semantics": (
            "stage0_relaxation_supercell"
            if config.sc_rlx
            else "stage0_relaxation_primitive"
        ),
        "n_sectors": list(config.n_sectors),
        "symm_reduce": bool(config.symm_reduce),
        "d": config.d,
        "d_mode": config.d_mode,
        "d_reference": _normalized_d_reference(config),
        "stackings": stacking_values,
        "inputs": inputs,
        "rlx_poscars": rlx_poscars,
    }


def stage1_structure_provenance(
    result: Stage1ProvenanceResult | None,
) -> dict[str, object]:
    if result is None or result.kind is None:
        return {}
    evidence: dict[str, object] = {}
    for key, value in result.evidence:
        if key == "stackings":
            evidence[key] = [[int(i), int(j)] for i, j in value]
        else:
            evidence[key] = value
    return {
        "mode": result.kind,
        "sc_rlx": result.trusted_sc_rlx,
        "sc": list(result.trusted_sc) if result.trusted_sc is not None else None,
        "evidence": evidence,
    }


def _append_issue(
    issues: list[ProvenanceIssue],
    path: Path,
    reason: str,
    *,
    severity: Literal["error", "warning"] = "error",
) -> None:
    issues.append(ProvenanceIssue(severity=severity, path=Path(path), reason=reason))


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
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return _canonical_provenance_value(left) == _canonical_provenance_value(right)


def _coerce_provenance_pair(
    value: object,
    field: str,
    path: Path,
    issues: list[ProvenanceIssue],
) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        _append_issue(issues, path, f"{field} must contain exactly two integer values")
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        _append_issue(issues, path, f"{field} must contain exactly two integer values")
        return None
    return int(value[0]), int(value[1])


def _identity_from_record(
    record: object,
    path: Path,
    issues: list[ProvenanceIssue],
) -> StructureIdentity | None:
    if not isinstance(record, Mapping):
        _append_issue(issues, path, "structure identity record must be a mapping")
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
        _append_issue(issues, path, f"invalid structure identity record: {exc}")
        return None


def _append_identity_mismatch(
    expected: StructureIdentity,
    actual: StructureIdentity,
    path: Path,
    issues: list[ProvenanceIssue],
    label: str,
) -> None:
    differences = structure_identity_differences(expected, actual)
    if differences:
        _append_issue(issues, path, f"{label} identity mismatch: {', '.join(differences)}")


def _validate_strict_stage1_provenance(
    config: DPmoireLiteConfig,
    manifest: Manifest,
    stackings: tuple[tuple[int, int], ...],
    structures: StructureHandler | None,
    relaxation_atoms: Mapping[tuple[int, int], Atoms],
    issues: list[ProvenanceIssue],
) -> tuple[bool | None, tuple[int, int] | None]:
    start_issues = len(issues)
    manifest_path = Path("rlx") / "manifest.yaml"
    provenance = manifest.structure_provenance

    if provenance.get("schema") != STRICT_STRUCTURE_PROVENANCE_SCHEMA:
        _append_issue(
            issues,
            manifest_path,
            "unsupported structure provenance schema; expected "
            f"{STRICT_STRUCTURE_PROVENANCE_SCHEMA}",
        )
    if type(provenance.get("stage")) is not int or provenance.get("stage") != 0:
        _append_issue(issues, manifest_path, "structure provenance stage must be 0")

    manifest_stackings = [[int(i), int(j)] for i, j in stackings]
    if provenance.get("stackings") != manifest_stackings:
        _append_issue(
            issues,
            manifest_path,
            "stackings differ between relaxation manifest and structure "
            f"provenance: manifest={manifest_stackings!r}, "
            f"provenance={provenance.get('stackings')!r}",
        )
    expected_directories = [f"rlx/{i}_{j}" for i, j in stackings]
    if manifest.directories != expected_directories:
        _append_issue(
            issues,
            manifest_path,
            "relaxation manifest directories do not match its stacking list: "
            f"expected={expected_directories!r}, actual={manifest.directories!r}",
        )

    manifest_sc_rlx = provenance.get("sc_rlx")
    if not isinstance(manifest_sc_rlx, bool):
        _append_issue(issues, manifest_path, "sc_rlx must be a boolean in structure provenance")
    elif config.sc_rlx != manifest_sc_rlx:
        _append_issue(
            issues,
            manifest_path,
            "sc_rlx conflicts with Stage0 provenance: "
            f"manifest={manifest_sc_rlx!r}, current config={config.sc_rlx!r}",
        )

    manifest_sc = _coerce_provenance_pair(provenance.get("sc"), "sc", manifest_path, issues)
    manifest_n_sectors = _coerce_provenance_pair(
        provenance.get("n_sectors"), "n_sectors", manifest_path, issues
    )
    if manifest_sc_rlx is True and manifest_sc is not None and tuple(config.sc) != manifest_sc:
        _append_issue(
            issues,
            manifest_path,
            "sc conflicts with a supercell relaxation: "
            f"manifest={list(manifest_sc)!r}, current config={list(config.sc)!r}",
        )
    if manifest_n_sectors is not None and tuple(config.n_sectors) != manifest_n_sectors:
        _append_issue(
            issues,
            manifest_path,
            "n_sectors differs from Stage0 provenance: "
            f"manifest={list(manifest_n_sectors)!r}, "
            f"current config={list(config.n_sectors)!r}",
        )

    immutable_fields = {
        "symm_reduce": config.symm_reduce,
        "d": config.d,
        "d_mode": config.d_mode,
        "d_reference": _normalized_d_reference(config),
    }
    for field_name, current_value in immutable_fields.items():
        if field_name not in provenance:
            _append_issue(issues, manifest_path, f"missing immutable field {field_name}")
        elif not _provenance_values_equal(provenance[field_name], current_value):
            _append_issue(
                issues,
                manifest_path,
                f"{field_name} differs from Stage0 provenance: "
                f"manifest={provenance[field_name]!r}, current config={current_value!r}",
            )
    if isinstance(manifest_sc_rlx, bool):
        expected_semantics = (
            "stage0_relaxation_supercell"
            if manifest_sc_rlx
            else "stage0_relaxation_primitive"
        )
        if provenance.get("sc_semantics") != expected_semantics:
            _append_issue(
                issues,
                manifest_path,
                "sc_semantics is inconsistent with manifest sc_rlx: "
                f"expected={expected_semantics!r}, "
                f"actual={provenance.get('sc_semantics')!r}",
            )

    expected_inputs = {
        "input/top_layer.poscar",
        "input/bot_layer.poscar",
    }
    input_records = provenance.get("inputs")
    if not isinstance(input_records, Mapping):
        _append_issue(
            issues,
            manifest_path,
            "inputs must be a mapping containing both Stage0 layer POSCARs",
        )
    else:
        for relative_path in sorted(expected_inputs - set(input_records)):
            _append_issue(issues, Path(relative_path), "missing input identity record")
        for relative_path in sorted(set(input_records) - expected_inputs):
            _append_issue(issues, Path(relative_path), "unexpected input identity record")
        if (
            structures is not None
            and structures.top_atoms is not None
            and structures.bot_atoms is not None
        ):
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
                    _append_issue(
                        issues,
                        Path(relative_path),
                        "missing or invalid input identity record",
                    )
                    continue
                if record.get("path") != relative_path:
                    _append_issue(
                        issues,
                        Path(relative_path),
                        f"identity record path is {record.get('path')!r}",
                    )
                expected_identity = _identity_from_record(
                    record, Path(relative_path), issues
                )
                try:
                    actual_identity = structure_identity(source_path, atoms)
                except Exception as exc:
                    _append_issue(
                        issues,
                        Path(relative_path),
                        f"could not read current input identity: {exc}",
                    )
                else:
                    if expected_identity is not None:
                        _append_identity_mismatch(
                            expected_identity,
                            actual_identity,
                            Path(relative_path),
                            issues,
                            "input",
                        )

    expected_rlx_paths = {f"rlx/{i}_{j}/POSCAR" for i, j in stackings}
    rlx_records = provenance.get("rlx_poscars")
    if not isinstance(rlx_records, Mapping):
        _append_issue(
            issues,
            manifest_path,
            "rlx_poscars must be a mapping of every generated POSCAR",
        )
        rlx_records = {}
    else:
        for relative_path in sorted(expected_rlx_paths - set(rlx_records)):
            _append_issue(
                issues,
                Path(relative_path),
                "missing generated POSCAR identity record",
            )
        for relative_path in sorted(set(rlx_records) - expected_rlx_paths):
            _append_issue(
                issues,
                Path(relative_path),
                "unexpected generated POSCAR identity record",
            )

    stage0_poscar_identities: dict[str, StructureIdentity] = {}
    for i, j in stackings:
        relative_path = f"rlx/{i}_{j}/POSCAR"
        display_path = Path(relative_path)
        record = rlx_records.get(relative_path)
        if not isinstance(record, Mapping):
            _append_issue(
                issues,
                display_path,
                "missing or invalid generated POSCAR identity record",
            )
        else:
            if record.get("path") != relative_path:
                _append_issue(
                    issues,
                    display_path,
                    f"identity record path is {record.get('path')!r}",
                )
            expected_identity = _identity_from_record(record, display_path, issues)
            poscar = config.work_dir / relative_path
            try:
                actual_identity = structure_identity(poscar)
            except Exception as exc:
                _append_issue(
                    issues,
                    display_path,
                    f"could not read generated POSCAR identity: {exc}",
                )
            else:
                stage0_poscar_identities[relative_path] = actual_identity
                if expected_identity is not None:
                    _append_identity_mismatch(
                        expected_identity,
                        actual_identity,
                        display_path,
                        issues,
                        "generated POSCAR",
                    )

        contcar = config.work_dir / f"rlx/{i}_{j}/CONTCAR"
        contcar_atoms = relaxation_atoms.get((i, j))
        if relative_path not in stage0_poscar_identities or contcar_atoms is None:
            continue
        try:
            contcar_identity = structure_identity(contcar, contcar_atoms)
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
            _append_issue(
                issues,
                Path(f"rlx/{i}_{j}/CONTCAR"),
                "CONTCAR topology/composition mismatch: "
                f"{', '.join(topology_differences)}",
            )

    if len(issues) != start_issues:
        return None, None
    if not isinstance(manifest_sc_rlx, bool) or manifest_sc is None:
        return None, None
    trusted_sc = tuple(config.sc) if not manifest_sc_rlx else manifest_sc
    return manifest_sc_rlx, trusted_sc


def _preserved_anchor_constraints(
    record: Mapping,
    path: Path,
    issues: list[ProvenanceIssue],
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
        _append_issue(
            issues,
            path,
            "grid-shift anchor record must contain two distinct integer indices",
        )
        return None

    fixed_masks = record.get("fixed_masks")
    if not isinstance(fixed_masks, Mapping):
        _append_issue(
            issues,
            path,
            "grid-shift anchor fixed_masks must be a mapping",
        )
        return None

    normalized_masks: dict[int, object] = {}
    for raw_index, mask in fixed_masks.items():
        if isinstance(raw_index, bool):
            _append_issue(
                issues,
                path,
                "grid-shift anchor fixed_masks indices must be integers",
            )
            continue
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            _append_issue(
                issues,
                path,
                "grid-shift anchor fixed_masks indices must be integers",
            )
            continue
        if index in normalized_masks:
            _append_issue(
                issues,
                path,
                "grid-shift anchor fixed_masks contain duplicate indices",
            )
        normalized_masks[index] = mask

    expected_indices = {top_index, bottom_index}
    if set(normalized_masks) != expected_indices:
        _append_issue(
            issues,
            path,
            "grid-shift anchor fixed_masks must contain exactly the top and "
            f"bottom indices: expected={sorted(expected_indices)!r}, "
            f"actual={sorted(normalized_masks)!r}",
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
            _append_issue(
                issues,
                path,
                "grid-shift anchor fixed mask must be [true, true, false]",
            )
            return None
        expected[index] = list(mask)
    return expected


def _source_constraint_set(
    atoms: Atoms,
    path: Path,
    issues: list[ProvenanceIssue],
) -> dict[int, list[bool]]:
    constraints: dict[int, list[bool]] = {}
    for constraint in atoms.constraints:
        if not isinstance(constraint, FixScaled):
            _append_issue(
                issues,
                path,
                "source CONTCAR uses an unsupported constraint type; "
                "preserved anchors must use FixScaled",
            )
            continue
        indexes = np.asarray(getattr(constraint, "index", []), dtype=int).reshape(-1)
        mask = np.asarray(getattr(constraint, "mask", []), dtype=bool).reshape(-1)
        if indexes.size != 1 or mask.shape != (3,):
            _append_issue(
                issues,
                path,
                "source CONTCAR constraints must contain one atom and a "
                "three-component mask",
            )
            continue
        index = int(indexes[0])
        if index < 0 or index >= len(atoms) or index in constraints:
            _append_issue(
                issues,
                path,
                "source CONTCAR constraints contain invalid or duplicate anchor indices",
            )
            continue
        constraints[index] = mask.tolist()
    return constraints


def _validate_preserved_grid_shift_anchors(
    config: DPmoireLiteConfig,
    manifest: Manifest,
    stackings: tuple[tuple[int, int], ...],
    relaxation_atoms: Mapping[tuple[int, int], Atoms],
    issues: list[ProvenanceIssue],
) -> tuple[tuple[int, int], ...]:
    manifest_path = Path("rlx") / "manifest.yaml"
    anchors = manifest.grid_shift_anchors
    if not isinstance(anchors, Mapping):
        _append_issue(
            issues,
            manifest_path,
            "preserve_grid_shift_md requires grid_shift_anchors mapping",
        )
        return ()
    if not anchors:
        nested_anchors = manifest.structure_provenance.get("grid_shift_anchors")
        if isinstance(nested_anchors, Mapping) and nested_anchors:
            _append_issue(
                issues,
                manifest_path,
                "grid_shift_anchors exist only under structure_provenance; "
                "regenerate Stage0 to write top-level grid_shift_anchors "
                "before enabling preserve_grid_shift_md",
            )
        else:
            _append_issue(
                issues,
                manifest_path,
                "preserve_grid_shift_md requires grid_shift_anchors mapping",
            )
        return ()

    anchor_issue_start = len(issues)
    validated: list[tuple[int, int]] = []
    for i, j in stackings:
        record_issue_start = len(issues)
        relative_directory = f"rlx/{i}_{j}"
        display_path = Path(relative_directory)
        anchor_record = anchors.get(relative_directory)
        if not isinstance(anchor_record, Mapping):
            _append_issue(
                issues,
                display_path,
                "missing or invalid grid-shift anchor record",
            )
            continue

        expected_constraints = _preserved_anchor_constraints(
            anchor_record,
            display_path,
            issues,
        )
        poscar = config.work_dir / relative_directory / "POSCAR"
        try:
            poscar_atoms = read_vasp(poscar)
            poscar_identity = structure_identity(poscar, poscar_atoms)
        except Exception as exc:
            _append_issue(
                issues,
                display_path / "POSCAR",
                f"could not validate preserved-anchor POSCAR: {exc}",
            )
            continue

        expected_atom_count = anchor_record.get("atom_count")
        expected_hash = anchor_record.get("poscar_sha256")
        if (
            isinstance(expected_atom_count, bool)
            or not isinstance(expected_atom_count, int)
            or expected_atom_count != poscar_identity.atom_count
        ):
            _append_issue(
                issues,
                display_path / "POSCAR",
                "preserved grid-shift anchor atom_count does not match POSCAR: "
                f"anchor={expected_atom_count!r}, actual={poscar_identity.atom_count!r}",
            )
        if not isinstance(expected_hash, str) or expected_hash != poscar_identity.sha256:
            _append_issue(
                issues,
                display_path / "POSCAR",
                "preserved grid-shift anchor POSCAR hash does not match "
                f"the current POSCAR: anchor={expected_hash!r}, "
                f"actual={poscar_identity.sha256!r}",
            )

        contcar_atoms = relaxation_atoms.get((i, j))
        if contcar_atoms is None:
            continue

        if len(poscar_atoms) != len(contcar_atoms):
            _append_issue(
                issues,
                display_path / "CONTCAR",
                "preserved grid-shift anchor atom order/count mismatch: "
                f"POSCAR={len(poscar_atoms)}, CONTCAR={len(contcar_atoms)}",
            )
        elif poscar_atoms.get_chemical_symbols() != contcar_atoms.get_chemical_symbols():
            _append_issue(
                issues,
                display_path / "CONTCAR",
                "preserved grid-shift anchor atom order mismatch",
            )

        actual_constraints = _source_constraint_set(
            contcar_atoms,
            display_path / "CONTCAR",
            issues,
        )
        if expected_constraints is not None and actual_constraints != expected_constraints:
            _append_issue(
                issues,
                display_path / "CONTCAR",
                "source CONTCAR constraint set does not exactly match "
                "the two manifest grid-shift anchors: "
                f"expected={expected_constraints!r}, actual={actual_constraints!r}",
            )
        if len(issues) == record_issue_start:
            validated.append(
                (int(anchor_record["top_index"]), int(anchor_record["bottom_index"]))
            )

    if len(issues) != anchor_issue_start:
        return ()
    return tuple(validated)


def _legacy_composition(atoms) -> Counter[str]:
    return Counter(atoms.get_chemical_symbols())


def _infer_legacy_structure_source(
    primitive,
    candidate,
) -> tuple[bool, tuple[int, int] | None]:
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
        raise ValueError(
            f"supercell count/composition conflicts with its in-plane cell: {exc}"
        ) from exc
    matrix = relation.matrix
    if (
        matrix[0][1] != 0
        or matrix[1][0] != 0
        or matrix[0][0] <= 0
        or matrix[1][1] <= 0
    ):
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
    stackings: tuple[tuple[int, int], ...],
    structures: StructureHandler | None,
    relaxation_atoms: Mapping[tuple[int, int], Atoms],
    issues: list[ProvenanceIssue],
) -> tuple[bool | None, tuple[int, int] | None]:
    start_issues = len(issues)
    if structures is None or structures.new_struct is None:
        _append_issue(
            issues,
            Path("rlx") / "manifest.yaml",
            "legacy inference requires readable current input structures",
        )
        return None, None

    primitive = structures.new_struct
    conclusions: list[tuple[bool, tuple[int, int] | None]] = []
    for i, j in stackings:
        directory = Path("rlx") / f"{i}_{j}"
        poscar = config.work_dir / directory / "POSCAR"
        if not poscar.is_file():
            _append_issue(
                issues,
                directory / "POSCAR",
                "legacy inference requires the Stage0 POSCAR",
            )
            continue
        try:
            candidate = read_vasp(poscar)
        except Exception as exc:
            _append_issue(
                issues,
                directory / "POSCAR",
                f"legacy inference could not read POSCAR: {exc}",
            )
            continue
        try:
            conclusion = _infer_legacy_structure_source(primitive, candidate)
        except ValueError as exc:
            _append_issue(
                issues,
                directory / "POSCAR",
                f"legacy inference failed: {exc}",
            )
            continue

        contcar_atoms = relaxation_atoms.get((i, j))
        if contcar_atoms is None:
            continue
        topology_differences = _legacy_topology_differences(candidate, contcar_atoms)
        if topology_differences:
            _append_issue(
                issues,
                directory / "CONTCAR",
                "legacy inference CONTCAR topology/composition mismatch: "
                f"{', '.join(topology_differences)}",
            )
            continue
        conclusions.append(conclusion)

    if conclusions:
        inferred_modes = {mode for mode, _ in conclusions}
        inferred_supercells = {sc for mode, sc in conclusions if mode and sc is not None}
        if len(inferred_modes) != 1:
            _append_issue(
                issues,
                Path("rlx") / "manifest.yaml",
                "legacy inference produced inconsistent sc_rlx conclusions across stackings",
            )
        elif True in inferred_modes and len(inferred_supercells) != 1:
            _append_issue(
                issues,
                Path("rlx") / "manifest.yaml",
                "legacy inference produced inconsistent supercell directions across stackings",
            )

    if len(issues) != start_issues:
        return None, None
    if not conclusions:
        _append_issue(
            issues,
            Path("rlx") / "manifest.yaml",
            "legacy inference found no usable stacking evidence",
        )
        return None, None

    inferred_sc_rlx = conclusions[0][0]
    inferred_sc = conclusions[0][1]
    if config.sc_rlx != inferred_sc_rlx:
        _append_issue(
            issues,
            Path("rlx") / "manifest.yaml",
            "Legacy inference conflicts with current config: "
            f"inferred sc_rlx={inferred_sc_rlx!r}, current sc_rlx={config.sc_rlx!r}",
        )
    elif inferred_sc_rlx and inferred_sc is not None and tuple(config.sc) != inferred_sc:
        _append_issue(
            issues,
            Path("rlx") / "manifest.yaml",
            "Legacy inference conflicts with current config: "
            f"inferred sc={list(inferred_sc)!r}, current sc={list(config.sc)!r}",
        )
    if len(issues) != start_issues:
        return None, None
    return inferred_sc_rlx, tuple(config.sc) if not inferred_sc_rlx else inferred_sc


def validate_stage1_provenance(
    config: DPmoireLiteConfig,
    manifest_result: ManifestReadResult,
    stackings: tuple[tuple[int, int], ...],
    structures: StructureHandler | None,
    relaxation_atoms: Mapping[tuple[int, int], Atoms],
) -> Stage1ProvenanceResult:
    issues: list[ProvenanceIssue] = []
    trusted_sc_rlx = None
    trusted_sc = None
    kind = None
    evidence: tuple[tuple[str, object], ...] = ()
    validated_anchor_indices: tuple[tuple[int, int], ...] = ()
    normalized_stackings = tuple((int(i), int(j)) for i, j in stackings)

    if manifest_result.kind == "legacy":
        trusted_sc_rlx, trusted_sc = _infer_legacy_stage1_provenance(
            config,
            normalized_stackings,
            structures,
            relaxation_atoms,
            issues,
        )
        if trusted_sc_rlx is not None:
            kind = "legacy_inference"
            evidence = (
                ("manifest", "rlx/manifest.yaml"),
                ("method", "atom_count_composition_cell"),
                ("stackings", normalized_stackings),
            )
            _append_issue(
                issues,
                Path("rlx") / "manifest.yaml",
                "Legacy relaxation provenance was inferred from atom count, "
                "composition, and in-plane cell; Stage1 is proceeding "
                "without strict Stage0 provenance.",
                severity="warning",
            )
    elif manifest_result.kind == "current" and manifest_result.manifest is not None:
        provenance = manifest_result.manifest.structure_provenance
        if provenance:
            trusted_sc_rlx, trusted_sc = _validate_strict_stage1_provenance(
                config,
                manifest_result.manifest,
                normalized_stackings,
                structures,
                relaxation_atoms,
                issues,
            )
            if trusted_sc_rlx is not None:
                kind = "strict"
                evidence = (
                    ("manifest", "rlx/manifest.yaml"),
                    ("schema", provenance.get("schema")),
                    ("stackings", normalized_stackings),
                )

    if config.preserve_grid_shift_md:
        if manifest_result.kind == "legacy":
            _append_issue(
                issues,
                Path("rlx") / "manifest.yaml",
                "preserve_grid_shift_md requires strict Stage0 provenance; "
                "legacy relaxation manifests cannot preserve grid-shift anchors",
            )
        elif manifest_result.kind == "current" and manifest_result.manifest is not None:
            provenance = manifest_result.manifest.structure_provenance
            if not provenance:
                _append_issue(
                    issues,
                    Path("rlx") / "manifest.yaml",
                    "preserve_grid_shift_md requires strict Stage0 provenance "
                    "with grid_shift_anchors",
                )
            else:
                validated_anchor_indices = _validate_preserved_grid_shift_anchors(
                    config,
                    manifest_result.manifest,
                    normalized_stackings,
                    relaxation_atoms,
                    issues,
                )
        else:
            _append_issue(
                issues,
                Path("rlx") / "manifest.yaml",
                "preserve_grid_shift_md requires a current strict Stage0 "
                "relaxation manifest with grid_shift_anchors",
            )

    return Stage1ProvenanceResult(
        kind=kind,
        trusted_sc_rlx=trusted_sc_rlx,
        trusted_sc=trusted_sc,
        evidence=evidence,
        validated_anchor_indices=validated_anchor_indices,
        issues=tuple(issues),
    )
