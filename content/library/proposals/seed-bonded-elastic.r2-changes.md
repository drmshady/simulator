# seed-bonded-elastic.yaml — revision 2 change log

Source of every change: full-text PDFs supplied by the owner on 2026-11-06. Revision 2 passes the same consistency
script as revision 1 (50 sources, all referenced, all medians and ranges consistent). Still **NOT APPROVED** (T040).

## A. Errors in revision 1 (fix regardless of other decisions)

| Entry | Was | Now | Why |
|---|---|---|---|
| `material.zirconia.E`, src-belli2017 | 201,400 (abstract) | 204,100 (Table 2, RUS #1) | "201.4" appears only in Belli's abstract; the six measurements in Table 2 are 204.1–210.4 GPa, and Wendler 2017 Table 1 gives the average as 205.2. Abstract error. Median unchanged (209,300). |
| `material.feldspathic.flexural_strength`, src-wendler2017 | 110.9, "B3B" (typical 110.9) | 110.93, "4-point bending" (typical 110.93, the median) | Wendler's abstract puts 110.9 among the B3B results, but Table 2 shows it is the 4-PB value; B3B was 118.78 / 118.65. Number right, method wrong. |
| `material.zirconia.flexural_strength`, src-wendler2017 | 1303.21, "B3B plates or discs" | 1030.30 (4-PB) | 1303.21 is plates only (discs 1240.89). Switched to the 4-PB bar value so that Wendler uses one test method in every entry, the method closest to the bar tests of the other sources. 1303.21 is kept as range max. |
| `frost.mild_overload_upper`, src-suedam2016 | note took "4000, citing Frost 2004" at face value | caution added | Frost 2004 Table 2 gives MESp **≈ 3000**, not 4000. Suedam's attribution is wrong. |
| All Wendler values | described as strengths | "Weibull characteristic strength σ₀" | Wendler reports σ₀ with 90% CIs, not means. |

## B. New values added

| Entry | Source | Value | Effect |
|---|---|---|---|
| `bone.cortical.E` | Schwartz-Dabney 2003 | 22,800 (grand mean E3, replaces "20,000 lower end of 20–30") | Median 14,850 → 15,000 |
| `bone.cortical.E` | Dechow 1993 *(new source)* | 19,400 (E3, mandible) | |
| `bone.cortical.nu` | Schwartz-Dabney 2003 | 0.28 (ν23) | **No longer WEAK.** Two measured mandible values (0.28, 0.273) agree with the FEA 0.30. Range min → 0.273 |
| `bone.cortical.nu` | Dechow 1993 | 0.273 (ν23) | |
| `material.zirconia.nu` | Belli 2017 | 0.242 (RUS) | Range min → 0.242 |
| `material.lithium_disilicate.E` | Belli 2017 | 102,800 | Median 98,070 → 100,000 |
| `material.lithium_disilicate.nu` | Belli 2017 | 0.214 | Confirms 0.22 |
| `material.feldspathic.E` | Belli 2017 | 72,400 (Mark II) | Range max 66,700 → 72,400 |
| `material.feldspathic.nu` | Belli 2017 | 0.23 | Confirms 0.21–0.23; Capobianco's 0.15 now an outlier |
| `material.resin_cement.E` | Barbon 2019 *(new source)* | 10,700 (RelyX Veneer, impulse excitation) | Median 7,000 → 8,850 |
| `material.zirconia.flexural_strength` | Miranda 2018 | 815.4 | Typical 861 → 850.5 (median); range min 840 → 815.4 |
| `material.lithium_disilicate.flexural_strength` | Wendler 2017 | 462.06 (e.max CAD, 4-PB) | Typical 368 → 396 (median); range max 440 → 462.06 |
| `load.bite_force.molar` | Varga 2011 *(new source)* | 777.7 (18-yr men) | Median 724.4 → 777.7 |
| `frost.disuse_upper` | Frost 2004 *(new source)* | 50 | Median 75 → 50 |
| `frost.adapted_upper` | Frost 2004 | 1000 (lower end of MESm 1000–1500) | Range min 1500 → 1000 |
| `frost.mild_overload_upper` | Frost 2004 | 3000 | Median 4000 → 3500; typical 3000 now has two primary sources |

Notes were also expanded, without value changes, for: `composite.E` (Belli Table 2 values and reliability caveat), `composite.nu` (Belli's ν values, still excluded), `resin_cement.nu` (Barbon's ν rejected: SD 0.24 and one value of 0.61 > 0.5), `cortical.thickness.D1` (Ma 2014 quote).

Source records: added `src-dechow1993`, `src-frost2004` (PMID only, as its SICI DOI is not at Crossref), `src-barbon2019`, `src-varga2011`; `src-sathyanarayana2012` stays PMID only: its DOI resolves at Crossref with the authors in the wrong order (a metadata error), which the source checker reports as a mismatch that blocks approval (owner decision, 2026-11-06; the DOI is recorded in the YAML header).

## C. Owner decisions (closed 2026-11-06)

1. **`material.zirconia.nu`: typical 0.32 → 0.242** (Belli RUS). Pulse-echo reads high for zirconia; 0.32 stays in range for sensitivity runs. Output effect is small (a few % in zirconia stress).
2. **`material.resin_cement.E`: typical kept at 7,000.** The dynamic values (10,700–13,630) likely overstate stiffness under chewing because resin cements are viscoelastic. Cement-stress outputs should be run across the range.
3. **`material.composite.flexural_strength`: Chung 2004b dropped.** Typical 162 → 175, range 66.61–219 → 149–219. `src-chung2004b` was then unused and has been removed from `sources` and the header table (50 sources now); the note keeps its PMID so it can seed a separate direct-composite entry if needed.

Not flagged but worth a look: `lithium_disilicate.E` typical 96,140 sits below the new median (100,000); `feldspathic.E` typical 65,535 vs Mark II block ≈ 71–72 GPa.

## D. Still open

- **Schettini 2025** and **Al Jabbari 2024** PDFs were not in this batch: cast/milled Co-Cr yield and per-alloy E remain abstract-only.
- Dechow 1993 Table 4 is a poor scan; the ν subscripts were read in the paper's stated order (12, 13, 21, 23, 31, 32). The chosen ν23 = 0.273 agrees with Schwartz-Dabney's 0.28, which supports the reading.
