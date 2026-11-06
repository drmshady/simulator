# CalculiX solver setup (T011, T059)

The engine drives **CalculiX ccx 2.23** as a separate program (research R3). The main PC uses the open-source
Windows build `ccx_static.exe`, whose sparse direct solver is **PaStiX**. The engine finds it through the
`DENTALSIM_CCX` environment variable.

| Item | Value |
|---|---|
| Version | ccx 2.23 (released 19 Oct 2025) |
| Program | `ccx_static.exe`: PaStiX 6.4 and SPOOLES, with Scotch, hwloc and OpenBLAS built in; all open source |
| Folder | `D:\DentalSimTools\calculix\` |
| Setting | `DENTALSIM_CCX=D:\DentalSimTools\calculix\ccx_static.exe` |
| Check | `uv run dentalsim solver check`, then `uv run pytest engine/tests/benchmark -q` |

## 1. Get the program

The CalculiX author publishes Windows binaries on <http://www.dhondt.de/> ("for a Windows executable").

- **Download**: <http://www.dhondt.de/calculix_2.23_4win.zip>
- **Checked by Claude on 5 Nov 2026, on the main PC**: 61,675,985 bytes, server date 6 Nov 2025.
  - SHA-256 of the zip: `87fc4e1721df37370ec67d0a244bb14f9bf804b0a95df49e2ae7369d30ec72e8`
  - The zip's `README_Install` says the binaries were built by Rafal Brzegowy.

  | File in the zip | Equation solvers | Needs | SHA-256 |
  |---|---|---|---|
  | **`ccx_static.exe`** (use this) | PaStiX, SPOOLES | nothing else | `e18360eb32a5cd48f91a212c57069212cd8e27d2f798a599a78106bd0b3ecc1f` |
  | `ccx_dynamic.exe` | PaStiX, SPOOLES, PARDISO | Intel MKL `mkl_rt.2.dll` | `dc894b9cbb04d30ed3c19cac533f31c0596fa0604514a26314a9e7967f0e6669` |
  | `ccx_i4.exe` | TAUCS, SPOOLES, PARDISO | Intel MKL | `ae1c27ee8d8c5ee04119525548b9f39dc2c79bb9fcabf5a01d089097b74f37f1` |
  | `ccx_dynamic_i8.exe` | SPOOLES, PARDISO (64-bit integers) | Intel MKL | `a675885aa3fc794bc74157d208451c929175b5f81d5db223353078a7700be437` |

  `ccx_static.exe` imports only Windows system DLLs. The program contains PaStiX, Scotch, hwloc and OpenBLAS and
  no Intel MKL (checked by Claude from its import table and the libraries it names).

In PowerShell:

```powershell
New-Item -ItemType Directory -Force D:\DentalSimTools\calculix
Invoke-WebRequest http://www.dhondt.de/calculix_2.23_4win.zip -OutFile $env:TEMP\calculix_2.23_4win.zip
Get-FileHash $env:TEMP\calculix_2.23_4win.zip -Algorithm SHA256
Expand-Archive $env:TEMP\calculix_2.23_4win.zip -DestinationPath $env:TEMP\calculix
Copy-Item $env:TEMP\calculix\calculix_2.23_4win\ccx_static.exe D:\DentalSimTools\calculix\
Get-FileHash D:\DentalSimTools\calculix\ccx_static.exe -Algorithm SHA256
```

If a SHA-256 differs from the one above, the file was replaced on the server since 5 Nov 2026. That is not
necessarily wrong, but record the new value below: the solver build id follows the program's own SHA-256.

**Not used: the PARDISO programs.** They need Intel MKL, which is free but not open source, and the constitution
asks for open-source tools only (Scope). PaStiX was also faster on detailed models (below). If `DENTALSIM_CCX`
points at a program that loads MKL, `solver check` says so in a note.

## 2. Set DENTALSIM_CCX

```powershell
setx DENTALSIM_CCX "D:\DentalSimTools\calculix\ccx_static.exe"
```

`setx` only affects programs started afterwards: open a new terminal, and restart the studio server and worker.

## 3. Check it

```powershell
uv run dentalsim solver check
uv run pytest engine/tests/benchmark -q
```

`solver check` runs `ccx -v`, then solves a one-element C3D10 model with `*STATIC, SOLVER=PASTIX`. On the main PC,
with a copy of `ccx_static.exe` in Claude's work folder, it gave:

```text
version:  2.23
equation solver: PASTIX
SHA-256:  e18360eb32a5cd48f91a212c57069212cd8e27d2f798a599a78106bd0b3ecc1f
build id: ccx-2.23-pastix-sha256:e18360eb32a5cd48f91a212c57069212cd8e27d2f798a599a78106bd0b3ecc1f
```

The build id is part of every run's input hash (R14): replacing the program marks earlier runs out of date.

What other answers mean:

| Message | Meaning |
|---|---|
| `No CalculiX solver is configured. Set DENTALSIM_CCX ...` (exit 2) | Step 2 is missing, or the terminal was opened before `setx` |
| `... is not an existing file` (exit 2) | The path in `DENTALSIM_CCX` is wrong |
| `equation solver: SPOOLES (fallback: PaStiX not available)` | The program has no PaStiX; it works, about 3 times slower |
| A note that the program loads Intel MKL | `DENTALSIM_CCX` points at `ccx_dynamic.exe` or another MKL build; use `ccx_static.exe` |
| `the solver could not start because a DLL it needs is missing ...` | An MKL build without its DLLs (Windows exit code 0xC0000135); use `ccx_static.exe` |

## Why PaStiX: measured on the main PC (5 Nov 2026)

A 20 mm cube of 10-node tetrahedra, one unit load case, 8 solver threads (the heavy lane's count); wall time
including assembly and output; peak memory of the solver process.

| DOF | PARDISO (MKL) | PaStiX (`ccx_static.exe`) | SPOOLES |
|---|---|---|---|
| 48 k | 3.5 s, 0.35 GB | 3.5 s, 0.43 GB | 4.3 s, 0.39 GB |
| 165 k | 16.6 s, 1.7 GB | 19.7 s, 2.0 GB | 49.5 s, 1.8 GB |
| 387 k | 110 s, 5.4 GB | 69 s, 6.1 GB | — |
| 758 k | 308 s, 13.5 GB | 154 s, 14.4 GB | — |

- All three balance the support reactions against the load to about 1e-14 (relative).
- PaStiX in ccx 2.23 factors in single precision, then refines the solution in double precision until its error
  is below 1e-12 (it switches to full double precision if that fails). Its final refinement error here was 1e-16
  to 6e-14. The engine reads it as the step's relative residual (FR-023).
- Previews, on the preview lane's 2 threads, one contact point (3 unit cases): 1.7 s at 14 k DOF, 8.1 s at 48 k
  DOF. That is why the preview level targets 15–25 k DOF (research R7).
- Memory grows as about DOF^1.3, so 32 GB holds models up to about 1.2 M DOF (research R8).

## Fallback builds

Each fallback is recorded as a different solver build, so its runs never mix with PaStiX runs.

- **SPOOLES, same program**: chosen automatically when PaStiX fails the probe; about 3 times slower.
- **WSL2 Ubuntu `calculix-ccx`**: `sudo apt install calculix-ccx`. *Note: Ubuntu 24.04 and 26.04 package ccx
  2.21, not 2.23* (packages.ubuntu.com, 5 Nov 2026). The engine's runner can start it as `wsl ccx`, but
  `DENTALSIM_CCX` and `solver check` expect a Windows program, so this route is not set up.
- **conda-forge `calculix` 2.23** has a win-64 package. *Not checked: which equation solvers it includes.*

## Recorded on the main PC (T011)

Installed at `D:\DentalSimTools\calculix\ccx_static.exe`; `DENTALSIM_CCX` set with `setx` for the owner's user.

| Item | Value |
|---|---|
| Date | 2026-11-06 (installed by Claude at the owner's request) |
| Source (URL) | <http://www.dhondt.de/calculix_2.23_4win.zip> |
| SHA-256 of the downloaded zip | `87fc4e1721df37370ec67d0a244bb14f9bf804b0a95df49e2ae7369d30ec72e8` |
| `solver check`: version string | 2.23 |
| `solver check`: equation solver | PASTIX |
| `solver check`: SHA-256 of `ccx_static.exe` | `e18360eb32a5cd48f91a212c57069212cd8e27d2f798a599a78106bd0b3ecc1f` |
| `solver check`: build id | `ccx-2.23-pastix-sha256:e18360eb32a5cd48f91a212c57069212cd8e27d2f798a599a78106bd0b3ecc1f` |
| Smoke test (`pytest engine/tests/benchmark`) passed | yes, 3 of 3 (2026-11-06) |

## Things learned while checking the build (main PC, 5 Nov 2026)

- `ccx -v` prints `This is Version 2.23` and exits with code 201; that is normal for ccx.
- **A model that is not fully supported still "solves".** A cube held only along one edge gave no message and
  displacements of 10⁹–10¹⁰ mm with every equation solver. The engine's own checks catch it (FR-023): force
  balance, the energy check (work of the load against twice the strain energy: 1.0000003 when supported, about
  −10¹¹ when hinged) and the PaStiX residual (it stopped at 2e-6 after 50 iterations, against about 1e-14
  normally). The runner also reports PaStiX's static pivots.
- **Every unit case is a full factorisation.** ccx assembles and factorises the stiffness matrix again in every
  `*STEP`; 3 cases took 2.8 times as long as one. The estimate model (`dentalsim/estimate/model.py`) is fitted per
  unit case for this reason (research R4).
- When its output goes to a pipe, ccx releases most progress messages in bursts at the end of each step, so the
  studio's progress moves step by step.

## Calibration (T059)

Run on the main PC, with nothing else heavy running (about 10 minutes), after `solver check` passes:

```powershell
uv run python -m dentalsim.estimate.calibrate
```

It meshes a 20 mm box at 1.6, 1.0 and 0.6 mm (about 48 k, 165 k and 760 k DOF), solves 3 unit load cases at each
size with the heavy lane's thread count (physical cores minus 2: 8 on the main PC; `dentalsim.threads`), and
writes `D:\DentalSimData\estimate\coefficients.json`. `--sizes 2,1.2,0.8` changes the sizes and `--threads N` the
thread count. It prints a table like the one below; copy it here.

**Preliminary run by Claude, 6 Nov 2026, main PC** (a copy of `ccx_static.exe` in Claude's work folder, coefficients
saved to a scratch data folder, not `D:\DentalSimData`). The owner repeats it after T011 so the coefficients land in
the real data folder; the numbers should match.

Solver build: `ccx-2.23-pastix-sha256:e18360eb32a5cd48f91a212c57069212cd8e27d2f798a599a78106bd0b3ecc1f`

Threads: 8

| Size (mm) | Nodes | Elements | DOF | Cases | Threads | Wall time (s) | Peak memory (MB) | Disk (MB) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.6 | 16,145 | 10,333 | 48,435 | 3 | 8 | 5.78 | 552 | 37.4 |
| 1.0 | 54,837 | 37,176 | 164,511 | 3 | 8 | 31.78 | 2,426 | 134.0 |
| 0.6 | 252,606 | 178,906 | 757,818 | 3 | 8 | 341.28 | 16,608 | 643.9 |

Fit (power law):

- time = cases × 2.025e-07 × DOF^1.4857 s
- memory = 900.5 × DOF^1.2386 bytes
- disk = 283.6 × DOF × cases bytes

What it predicts: a single contact point (3 cases) at 758 k DOF takes about 5.7 min; memory reaches the 27 GB
limit (85% of 31.8 GB) at about 1.1 M DOF.

Owner's run after T011: *recorded by the owner*

## A rented solver machine (optional)

When a model does not fit or is too slow on the main PC, the owner may rent a more powerful machine and send only
the solver step to it over SSH (constitution 2.1.0, Scope; spec FR-064; research R23; tasks Phase 14). On
Windows, copy the same `ccx_static.exe`; on Linux, build ccx 2.23 with PaStiX from source. Its own build must pass
`dentalsim verify` there before its results are used. The setup will be recorded in `docs/setup/rented-solver.md`.
