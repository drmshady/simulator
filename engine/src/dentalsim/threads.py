"""How many solver threads each queue lane gets (research R9). The worker and the calibration runs both use this,
so the estimate coefficients are measured with the thread count real runs get.

- Heavy jobs (detailed runs, studies, verification): every physical core but 2, at least 1, so previews stay fast.
- Previews: 2 threads.

Physical cores, not logical ones: the sparse direct solvers gain nothing from hyper-threads.
"""

from __future__ import annotations

import os

import psutil

HEAVY_RESERVED_CORES = 2
PREVIEW_THREADS = 2


def physical_cores() -> int:
    return psutil.cpu_count(logical=False) or os.cpu_count() or 1


def heavy_threads() -> int:
    return max(1, physical_cores() - HEAVY_RESERVED_CORES)


def preview_threads() -> int:
    return PREVIEW_THREADS
