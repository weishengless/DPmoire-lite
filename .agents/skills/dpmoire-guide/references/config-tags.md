# Tags and Configuration Keys

The word "tag" has three different meanings here. Name the namespace whenever
you use it.

## Skill Invocation

- Codex: `$dpmoire-guide`
- ChatGPT clients that support skills: `@dpmoire-guide`
- Other agents: attach or import this skill folder, then explicitly instruct the
  agent to read `SKILL.md`. Do not assume a vendor-specific invocation syntax.

## Task Routing Tags

- `[LEARN]`: read-only explanation.
- `[RUN]`: authorized package operation.
- `[DEBUG]`: diagnosis without a fix.
- `[CHANGE]`: authorized repository or configuration change.

These labels do not change package behavior and do not grant permission to
write, delete, or submit.

## `config.yaml` Keys

Use the current `README.md` section **Config Tags** and the comments in
`example/config.yaml` as the human-facing authority. Confirm validation rules in
`src/dpmoire_lite/config.py` when changing behavior.

Group keys before explaining them:

| Group | Keys |
| --- | --- |
| Files and resources | `dft_script`, `potcar_dir`, `potcar_policy`, `script_dir`, `input_dir`, `work_dir`, `n_nodes` |
| Execution controls | `stage`, `submit`, `auto_resub` |
| Engine and collection | `vasp_ml`, `outcar_collect_freq`, `outcar_patterns` |
| Init and stage generation | `do_relaxation`, `init_mlff`, `init_mlff_mode`, `init_bottom_incar`, `init_top_incar`, `sc_rlx`, `include_monolayer_md` |
| Geometry and sampling | `n_sectors`, `sc`, `d`, `d_mode`, `d_reference`, `k_mesh`, `encut_factor`, `r_cut`, `symm_reduce`, `twist_val`, `min_val_n`, `max_val_n`, `preserve_grid_shift_md`, `grid_shift_anchor` |

## Configuration Traps

- Keys are snake_case. Old names such as `VASP_ML`, `K-mesh`, `POTCAR_dir`,
  `DFT_script`, `ENMAX`, and `OUTCAR_collect_freq` are rejected.
- `stage: all` parses as a known value but build deliberately rejects it.
- `submit: true` causes real Slurm requests; it is not a dry-run flag.
- `auto_resub` and `n_nodes` do not enable the currently disabled submitted
  wait workflow.
- `init_mlff_mode: single-job` requires explicit bottom/top INCAR templates and
  a submit-script marker contract.
- `outcar_patterns` must be a non-empty YAML list of valid non-empty regex
  strings. It does not control MLFF MD collection.
- `preserve_grid_shift_md: true` preserves only DPmoire-lite grid-shift anchors;
  it is not a request to preserve every selective-dynamics constraint.
- `grid_shift_anchor` pins exactly one anti-slide anchor atom per layer, chosen
  by element (`top`/`bot` mapping, or one bare symbol for both layers). The
  element must exist in that layer and is validated before any directory is
  created. Without the key, the default stays the sorted first atom of each
  layer. `selection: nearest_pair` switches the within-layer pick to the
  closest-approaching cross-layer pair of the selected elements (ties to the
  lowest atom index); the bare keyword `nearest_pair` skips element filtering.
- `potcar_policy` selects POTCAR variants, but POTCAR bytes remain private and
  must never enter prompts, fixtures, logs, packages, or git.

When a key's meaning matters to a calculation, quote its observed value, say
which stage consumes it, and point to the current authoritative source.
