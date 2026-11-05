# Contract: Literature entry proposal and snapshot format (YAML)

This format is used for two things:

- **Bulk proposals**: files in `content/library/proposals/` (FR-053), for example ones Claude prepares from
  literature searches.
- **Approved-library snapshots**: files in `content/library/snapshots/`, written by `dentalsim library export`.

Importing always creates versions with status `proposed`, whatever the file says. Only the studio approval step
can approve (FR-014).

```yaml
format: dentalsim.library/1
prepared_by: claude            # claude | owner
prepared_at: 2026-11-05
entries:
  - parameter_key: bone.cancellous.E.D4
    category: bone             # material | bone | load | strength | frost_window
    label: Cancellous bone elastic modulus, density class D4
    units: MPa
    typical: 690
    range: {min: 200, max: 1400}
    values_by_source:
      - source: src-example-a
        value: 690
        note: "Mean of the reported D4 range."
    notes: "Linear isotropic model; the range covers the studies listed."
sources:
  - id: src-example-a
    authors: ["Author AB", "Author CD"]
    year: 2000
    title: "Example title of the cited paper"
    journal: "Example Journal"
    doi: "10.0000/example"      # doi and/or pmid; at least one is required
    pmid: null
    study_type: experimental
```

The numbers and the source above are placeholders that only show the format. They are not proposed values.

## Validation on import

- **Required fields**: `parameter_key`, `category`, `units`, `typical`, `range`, and at least one source with a
  DOI or a PMID.
- **Range**: `range.min ≤ typical ≤ range.max`, and every `values_by_source.value` lies within the range.
- **Units**: must match the dimension the conventions document gives for that parameter category. For example, a
  modulus must be in MPa.
- **Repeated parameter**: a `parameter_key` that already exists becomes a new proposed version of that entry,
  not a duplicate.
- **Source check**: sources are not checked during import. The check (FR-054) runs before approval in the
  studio or with `dentalsim library check`.
