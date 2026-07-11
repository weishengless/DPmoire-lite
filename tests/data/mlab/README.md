# ML_AB/ML_ABN Portable Fixture Inventory

These files are the minimal, sanitized fixture corpus owned by Plan 03. They
use the portable ML_AB/ML_ABN block labels needed by the structured parser and
contain no POTCAR, basis-set tables from a calculation, or licensed potential
content.

Every entry records the required provenance and expected parser boundary.

## `complete_multi.mlab`

- **Source type:** synthetic ML_ABN-style source with two complete configurations.
- **Reason for cropping:** smallest multi-configuration complete source for order and count tests.
- **Retained blocks:** portable header plus two complete lattice, position, energy, force, and stress records.
- **Removed private data:** no paths, users, hosts, jobs, accounts, cluster details, or unrelated output.
- **Redistribution confirmation:** all values are synthetic and contain no POTCAR or licensed potential content.
- **Expected parser behavior:** complete; two accepted configurations.

## `tail_position_crop.mlab`

- **Source type:** constructed crop from the synthetic complete source.
- **Reason for cropping:** the final configuration ends inside its Cartesian position block.
- **Retained blocks:** complete header and first configuration; final configuration through a partial position record.
- **Removed private data:** no paths, users, hosts, jobs, accounts, cluster details, or unrelated output.
- **Redistribution confirmation:** controlled numeric crop with no POTCAR or licensed potential content.
- **Expected parser behavior:** tail-partial; one accepted configuration and one discarded final configuration.

## `tail_force_crop.mlab`

- **Source type:** constructed crop from the synthetic complete source.
- **Reason for cropping:** the final configuration ends inside its force block.
- **Retained blocks:** complete header and first configuration; final configuration through a partial force record.
- **Removed private data:** no paths, users, hosts, jobs, accounts, cluster details, or unrelated output.
- **Redistribution confirmation:** controlled numeric crop with no POTCAR or licensed potential content.
- **Expected parser behavior:** tail-partial; one accepted configuration and one discarded final configuration.

## `tail_stress_crop.mlab`

- **Source type:** constructed crop from the synthetic complete source.
- **Reason for cropping:** the final configuration ends inside its stress block.
- **Retained blocks:** complete header and first configuration; final configuration through a partial stress record.
- **Removed private data:** no paths, users, hosts, jobs, accounts, cluster details, or unrelated output.
- **Redistribution confirmation:** controlled numeric crop with no POTCAR or licensed potential content.
- **Expected parser behavior:** tail-partial; one accepted configuration and one discarded final configuration.

## `first_incomplete.mlab`

- **Source type:** constructed malformed ML_ABN-style source.
- **Reason for cropping:** the first configuration starts but ends before its position block is complete.
- **Retained blocks:** complete header and first configuration prefix through an empty position block.
- **Removed private data:** no paths, users, hosts, jobs, accounts, cluster details, or unrelated output.
- **Redistribution confirmation:** controlled synthetic failure case with no POTCAR or licensed potential content.
- **Expected parser behavior:** failure; zero accepted configurations.

## `internal_corruption.mlab`

- **Source type:** constructed malformed ML_ABN-style source.
- **Reason for cropping:** a corrupted configuration is followed by another recognizable configuration marker.
- **Retained blocks:** complete first configuration; corrupted second position record; later configuration marker.
- **Removed private data:** no paths, users, hosts, jobs, accounts, cluster details, or unrelated output.
- **Redistribution confirmation:** controlled synthetic failure case with no POTCAR or licensed potential content.
- **Expected parser behavior:** internal-corruption failure; do not salvage the earlier prefix.

## `format_variant_a.mlab` and `format_variant_b.mlab`

- **Source type:** paired synthetic ML_ABN-style sources.
- **Reason for cropping:** identical scientific content with different harmless whitespace and float formatting.
- **Retained blocks:** complete portable header and one complete lattice, position, energy, force, and stress record in each variant.
- **Removed private data:** no paths, users, hosts, jobs, accounts, cluster details, or unrelated output.
- **Redistribution confirmation:** synthetic numeric values with no POTCAR or licensed potential content.
- **Expected parser behavior:** each is complete with one configuration; canonical identity is equal.

## `complete_vasp_641.mlab`

- **Source type:** synthetic portable minimum based on the VASP 6.4.1 ML_ABN/OUTCAR format evidence in the ignored local corpus.
- **Reason for cropping:** retain one complete configuration and the minimum header/block labels needed to exercise the 6.4.1 parser boundary.
- **Retained blocks:** portable header plus one complete lattice, position, energy, force, and stress record.
- **Removed private data:** the raw calculation path, account, host, job metadata, unrelated output, and all potential data were removed.
- **Redistribution confirmation:** the committed file is a newly written minimal scientific block; it contains no POTCAR or licensed potential content.
- **Expected parser behavior:** complete; one accepted configuration.

## `complete_vasp_651.mlab`

- **Source type:** synthetic portable minimum based on the VASP 6.5.1 ML_ABN/OUTCAR format evidence in the ignored local corpus.
- **Reason for cropping:** retain one complete two-type configuration and the minimum header/block labels needed to exercise the 6.5.1 parser boundary.
- **Retained blocks:** portable two-type header plus one complete lattice, position, energy, force, and stress record.
- **Removed private data:** the raw calculation path, account, host, job metadata, unrelated output, and all potential data were removed.
- **Redistribution confirmation:** the committed file is a newly written minimal scientific block; it contains no POTCAR or licensed potential content.
- **Expected parser behavior:** complete; one accepted configuration.

The VASP version labels are provenance labels from the associated local OUTCAR
evidence, not executable-version claims encoded in the ML_ABN header. The raw
evidence remains ignored and is never a test dependency.
