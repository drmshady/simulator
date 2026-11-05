# Contract: `dentalsim` command line

This is the command-line equivalent of the studio that Principle VI requires.

- Every output the studio makes can be made from here, with identical results, because both call the same engine
  functions.
- **Exit codes**:

  | Code | Meaning |
  |---|---|
  | 0 | Success |
  | 1 | Refused: invalid assembly, missing benchmark, failed checklist |
  | 2 | Error |

- **Output**: human-readable text by default. `--json` gives machine-readable output.

## Commands

| Command | Purpose |
|---|---|
| `dentalsim studio serve [--host IP] [--port 8443]` | Starts the server and worker on the main PC (R10) |
| `dentalsim studio set-password` | Sets or changes the owner password (prompted; stored as an Argon2id hash) |
| `dentalsim worker` | Starts the worker alone (normally started by `serve`) |
| `dentalsim solver check` | Runs the configured CalculiX binary once and prints its version, the PARDISO status and the SHA-256 (the solver build id) |
| `dentalsim conventions check` | Parses `docs/conventions.md`, validates it, prints the version and hash (SC-014) |
| `dentalsim validate <scenario.json>` | Validity checks and the problem list |
| `dentalsim estimate <scenario.json> --detail standard` | Expected DOF, time, memory and disk |
| `dentalsim run <scenario.json> --detail standard [--out DIR]` | Solves; prints the key numbers and run id |
| `dentalsim preview <scenario.json>` | Preview solve |
| `dentalsim mesh-check <scenario.json> --levels standard,fine` | FR-037 |
| `dentalsim study <study.yaml>` | Queues a sweep or sensitivity study |
| `dentalsim verify [--case ID ...] [--physics bonded_elastic]` | Runs the verification suite; writes the report; updates capability status |
| `dentalsim library import <file.yaml>` / `library check <entry>` / `library approve <entry> <version>` / `library reject ...` / `library export` | Literature library operations (same rules as the studio) |
| `dentalsim export <run-id> --kind figure,film,slide_loop,pptx,report,spreadsheet` | Exports |
| `dentalsim regenerate <export-id>` | Rebuilds and compares with the stored export (FR-044) |
| `dentalsim interactive-data <scenario-id> --controls controls.yaml` | FR-063 |
| `dentalsim publish <scenario-id>` / `unpublish <slug>` | Publishing (FR-045, FR-047) |
| `dentalsim promo <slug> --script script.txt` | Promo clip (FR-041) |
| `dentalsim backup [--to PATH]` / `restore <archive>` | FR-034 |
| `dentalsim check` | Full merge gate: lint, type checks, unit, integration, benchmark and regression tests. Without the solver the result is "incomplete" (exit 1), which is not a pass |
| `dentalsim check --quick` | Fast feedback without benchmark and regression tests; never a merge gate and records no pass |

## Scenario files

`<scenario.json>` uses the scenario format in [scenario-format.md](scenario-format.md). The command line accepts
a file or a studio scenario id (`--id`).
