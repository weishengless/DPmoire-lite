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

## `seed_input_vasp_641.mlab` and `seed_rewrite_vasp_641.mlab`

- **Source type:** paired, synthetic one-atom ML_AB-style sources representing an initial seed and the prefix rewritten by VASP 6.4.1.
- **Paired provenance:** the associated ignored OUTCAR identifies VASP 6.4.1; the ignored input/output pair contained 78/110 configurations and preserved structure and ordering across the 78-configuration prefix.
- **Constructed transformation:** the committed pair was newly written from the existing portable carbon fixture, with only one position scalar and one force scalar changed by the measured pair maxima (`8.673617379884036e-19` angstrom and `6.938893903907228e-18` eV/angstrom). It is not a copied raw configuration.
- **Reason for cropping:** retain the smallest redistributable pair that proves a VASP seed rewrite can change canonical numeric content without changing structure or order.
- **Retained blocks:** portable header plus one complete lattice, position, energy, force, and stress record in each file.
- **Removed private data:** raw structures, paths, users, hosts, jobs, accounts, cluster details, unrelated output, and all potential data were excluded.
- **Redistribution confirmation:** all scientific values except the two measured delta magnitudes are synthetic; neither file contains potential content or calculation identifiers.
- **Expected parser behavior:** both files are complete with one configuration; canonical identities differ, while structure and ordering match exactly.

## `seed_input_vasp_651.mlab` and `seed_rewrite_vasp_651.mlab`

- **Source type:** paired, synthetic two-atom ML_AB-style sources representing an initial seed and the prefix rewritten by VASP 6.5.1.
- **Paired provenance:** the associated ignored OUTCAR identifies VASP 6.5.1; the ignored input/output pair contained 63/294 configurations and preserved structure and ordering across the 63-configuration prefix.
- **Constructed transformation:** the committed pair was newly written from the existing portable O/Pt fixture, with only one position scalar and one force scalar changed by the measured pair maxima (`1.012523398458143e-13` angstrom and `1.3877787807814457e-17` eV/angstrom). It is not a copied raw configuration.
- **Reason for cropping:** retain the smallest redistributable pair that proves the observed 6.5.1 seed rewrite while preserving an independently checkable scientific boundary.
- **Retained blocks:** portable two-type header plus one complete lattice, position, energy, force, and stress record in each file.
- **Removed private data:** raw structures, paths, users, hosts, jobs, accounts, cluster details, unrelated output, and all potential data were excluded.
- **Redistribution confirmation:** all scientific values except the two measured delta magnitudes are synthetic; neither file contains potential content or calculation identifiers.
- **Expected parser behavior:** both files are complete with one configuration; canonical identities differ, while structure and ordering match exactly.

## `seed_rewrite_postseed_vasp_651.mlab`

- **Source type:** constructed synthetic ML_ABN-style source containing the 6.5.1 rewrite fixture followed by one new configuration.
- **Paired provenance:** its first configuration is canonically identical to `seed_rewrite_vasp_651.mlab`; the VASP-version label inherits only that pair's ignored OUTCAR evidence.
- **Constructed transformation:** the first configuration is the sanitized rewrite member above, and the second is a distinct fully synthetic O/Pt configuration created for later collection-boundary tests.
- **Reason for cropping:** model the smallest final ML_ABN containing a rewritten seed prefix and one publishable post-seed configuration.
- **Retained blocks:** portable two-type header plus two complete lattice, position, energy, force, and stress records.
- **Removed private data:** no raw post-seed calculation was copied; paths, users, hosts, jobs, accounts, cluster details, unrelated output, and all potential data are absent.
- **Redistribution confirmation:** the source is constructed and redistributable, with no licensed potential content.
- **Expected parser behavior:** complete; two accepted configurations, with the first equal to the rewrite fixture and the second canonically distinct.

The VASP version labels are provenance labels from the associated local OUTCAR
evidence, not executable-version claims encoded in the ML_ABN header. The raw
evidence remains ignored and is never a test dependency.
