# Performance on the main PC (T098)

Measured by Claude on 6 Nov 2026 during the slice 1a acceptance (task T098), on the main PC, with the built-in
template "Single crown (implant)" (crown height changed from 7.5 to 8.0 mm). Times are the pipeline's own stage
timings (`timings_s` in each result); `uv run` start-up adds about 1 s on the command line, not in the studio.

## The machine

| Item | Value |
|---|---|
| CPU | Intel Core i5-13400F: 6 performance cores (12 threads) and 4 efficiency cores, 16 logical processors |
| Memory | 32 GB |
| System | Windows 11 Pro, power plan Balanced |
| Drives | C: NVMe SSD (Kingston, 1 TB); D: hard disk (Toshiba MQ01ABD100, 1 TB), which holds `D:\DentalSimData`; E: SATA SSD (Crucial MX500, 500 GB), which holds the repository |
| Solver | ccx 2.23 with PaStiX (`ccx_static.exe`), see [solver.md](solver.md); gmsh 4.15.2 |

## Results

| Check | Measured | Target |
|---|---|---|
| `dentalsim verify --physics bonded_elastic` (6 textbook cases, CalculiX and scikit-fem) | 2 min 12 s, all pass | — |
| Validity, fast checks (implant in the nerve canal) | 0.15 s | at once (FR-011) |
| Validity, full checks (with overlaps) | 1.4 s | — |
| Preview, active load case, 65,850 DOF, data on D: (hard disk) | 11.8–13.3 s; 13.1 s in the studio | about 13 s (SC-002) |
| Preview, active load case, data on C: (SSD) | 10.3–11.5 s | about 13 s (SC-002) |
| Standard run, both load cases (6 unit cases), 222,180 DOF | 2 min 35 s (2 min 43 s from the command line) | 30 min (SC-003) |
| Standard run from the studio (the owner's scenario) | 1 min 37 s | 30 min (SC-003) |

### Preview stages

| Stage | Seconds |
|---|---|
| geometry (assembly in OpenCASCADE) | 1.8 |
| mesh (gmsh, one thread) | 3.5–3.7 |
| solve (ccx, one unit case) | 4.2–5.5 |
| results surfaces (glTF) | 0.3 |
| store: on D: (hard disk) | 1.1–2.0 |
| store: on C: (SSD) | 0.1 |
| everything else | 0.3 |

### Standard run stages

geometry 3.5 s, mesh 25.3 s, solve 112.1 s, results surfaces 4.6 s, store 6.0 s (on D:), the rest 3.5 s: 155 s
in all. Peak solver memory 2.8 GB, disk 0.42 GB. The estimate shown before the run was 182,980 DOF and 84 s; the
mesh had 222,180 DOF (the DOF prediction was 18% low for this model), and the estimate covers the solve only, not
the 43 s of geometry, meshing and storing. The solve time itself matched the calibrated model for the actual DOF.
Each run refits the estimate.

## Findings

1. **Windows power throttling.** Windows 11 runs the threads of a process without a focused window on the
   efficiency cores. Pinned to the efficiency cores a preview took 23–24 s (mesh 7.1–7.3 s, solve 10.5–11.1 s);
   pinned to the performance cores 11.1–12.6 s; left to Windows, 16–18 s, because the single-threaded mesher
   landed on an efficiency core. A higher process priority did not help. Since T098 a preview's thread and its
   solver ask Windows not to throttle them (`engine/src/dentalsim/qos.py`); detailed runs keep the default, so
   they still give way to other work on the PC.
2. **The data folder is on a hard disk.** Writing a preview's results (compressed, each file flushed to disk)
   takes 1.1–2.0 s on D: and 0.1 s on an SSD, and a standard run's 6.0 s. With the data folder on D: a preview
   takes 11.8–13.3 s; on an SSD, 10.3–11.5 s. The data folder can be moved with `DENTALSIM_DATA` (it is 149 MB
   now); free space on 6 Nov 2026: C: 267 GB, E: 151 GB. **The owner kept it on D: and accepted a preview of
   about 13 s (SC-002, 6 Nov 2026).**

## Slice 1b templates (T099–T106; before the owner's check, T107)

Measured by Claude on 6 Nov 2026 on the main PC, from the command line, with the four slice 1b templates as
delivered (design defaults still awaiting the owner's approval). These are previews only (detail level `preview`,
2 solver threads); the standard runs, including the 3-implant run for SC-013, are the owner's check in T107.

| Template | Validity, fast / full | Geometry + mesh (unthrottled) | Preview DOF | Preview test, both load cases |
|---|---|---|---|---|
| Single crown (implant), for comparison | 0.01 s / 1.4 s | 5.4–5.8 s | 65,952 | — (11.8–13.3 s, one load case; above) |
| Splinted crowns (implants), 2 implants | 0.03 s / 1.9 s | 12.8 s | 121,200 | 32 s |
| Bridge (implants), 2 implants and a pontic | 0.08 s / 4.4 s | 32.7 s | 137,121 | 62 s |
| Cantilever bridge (implants), 2 implants and a cantilever | 0.08 s / 4.6 s | 14.7 s | 131,613 | 34 s |
| Full-arch fixed (implants), 4 implants, 6 pontics, 2 cantilevers | 0.2 s / 16 s | 85 s | 295,815 | 170 s |

"Preview test" is `test_each_template_preview_solves` (open the template, validate, join, mesh, solve both load
cases, results and store, data in a temporary folder on C:), so it is longer than one preview in the studio,
which solves only the active load case. The previews of slice 1b scenarios are therefore well above the 13 s of
SC-002, which was set and measured on the single implant crown. **The owner accepted these preview times on
6 Nov 2026** (SC-002 and FR-017 now say the 13 s target is the single crown's).

Two meshing changes came with slice 1b, both leaving the single crown's mesh identical:

1. **Refinement hints of the same size share one size field.** Each hint used to be its own field, evaluated
   wherever the mesher asks for a size, so meshing time grew with the number of implants times the model's size:
   the bridge's preview mesh went from 27 s to 14 s with the same mesh.
2. **A mesh with a badly shaped element is refined around it and meshed again**, up to 4 times, finer each time
   there (and so is one the mesher refuses for a folded surface mesh, around the surfaces it names). The slice 1a
   crown's cusp ridges end about 0.15 mm short of the edge of its occlusal table; in some positions along the arch
   that strip meshed into slivers (quality 0.009 against the 0.01 floor). The bridge's 32.7 s includes such
   rounds. A better crown shape (the owner's later spec on realistic crowns) would remove the strip.
