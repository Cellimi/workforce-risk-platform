"""Small-group suppression, enforced in the engine rather than the UI.

Three protections, in order of strength.

**1. Threshold suppression.** A cell backed by fewer than `min_cell_size` distinct employees
is not released. Its value goes into an "Other (suppressed)" bucket so totals still
reconcile -- the money is never deleted, only pooled.

**2. Complementary suppression.** A single suppressed cell in a row is not protected at all:
subtract the visible cells from the row total and there it is. So whenever a row or column
would be left with exactly one suppressed cell, the next-smallest cell is suppressed too.
This repeats until every row and column holds either zero or at least two suppressed cells.

**3. Differencing guard.** Two overlapping queries can isolate a small group even when
neither query suppresses anything: ask for 2024-2026, ask again for 2024-2025, subtract. The
ledger below defends against that. It remembers the *cohort* -- the set of employee ids --
behind every value released so far, and refuses to release a new cohort whose symmetric
difference with any earlier one is smaller than the threshold. Cohorts stay server-side and
are never part of a response.

Who the ledger belongs to
-------------------------
In a licensed installation there is one ledger per person per engagement, keyed to the
signed-in user and never to anything the caller sends. It is kept on disk in the
engagement's folder (`ledger_file.py`), so a restart does not wipe it. It lasts as long as
the engagement: a new or corrected client file, a changed client-specific value and a
different occupation group all move only some figures, and the rest stay comparable, so
the record carries across them. Only a different minimum cell size starts a new one. The
synthetic walkthrough keeps its ledgers in memory only.

Limits, stated plainly
----------------------
The ledger is a pairwise residual check, not a full query audit. It cannot see combinations
of three or more earlier answers. Two people who share what they were each shown are not
caught, because each has their own ledger. Full disclosure auditing is not Phase 1 work, and
the demo should not claim otherwise.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

DEFAULT_MIN_CELL_SIZE = 5


class SuppressionReason:
    THRESHOLD = "below_minimum_cell_size"
    COMPLEMENTARY = "complementary_suppression"
    DIFFERENCING = "differencing_guard"


@dataclass
class DisclosureLedger:
    """Remembers which cohorts have already been released, to block differencing attacks."""

    min_cell_size: int = DEFAULT_MIN_CELL_SIZE
    released: list[frozenset[str]] = field(default_factory=list)
    #: Called with each batch of newly released cohorts before the figures that carry them
    #: leave the server. If it raises, the request fails and nothing is released.
    keep: Callable[[list[frozenset[str]]], None] | None = None
    _seen: set[frozenset[str]] = field(default_factory=set, repr=False)
    _pending: list[frozenset[str]] | None = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self._seen = set(self.released)

    @contextmanager
    def releasing(self) -> Iterator[DisclosureLedger]:
        """Check and record a whole response as one step.

        Two requests from the same person at the same moment must not both pass the check
        before either records what it released, so they take turns here. What is recorded
        inside is kept, through `keep`, once, at the end.
        """
        with self._lock:
            outermost = self._pending is None
            if outermost:
                self._pending = []
            try:
                yield self
            finally:
                if outermost:
                    pending, self._pending = self._pending, None
                    if pending and self.keep is not None:
                        self.keep(pending)

    def would_expose(self, cohort: frozenset[str]) -> bool:
        """True when releasing `cohort` would let a caller isolate a group that is too small.

        A caller who holds the total for cohort L and is then given the total for cohort C
        can compute the totals for `C - L` and `L - C` by subtraction whenever one contains
        the other. Either residual being non-empty and under the threshold is a disclosure.
        """
        if not cohort or cohort in self._seen:
            # A group this person has already been given a figure for tells them nothing
            # new by being shown again.
            return False
        for prior in self.released:
            for residual in (cohort - prior, prior - cohort):
                if 0 < len(residual) < self.min_cell_size:
                    return True
        return False

    def record(self, cohort: frozenset[str]) -> None:
        if not cohort or cohort in self._seen:
            return
        self._seen.add(cohort)
        self.released.append(cohort)
        if self._pending is not None:
            self._pending.append(cohort)
        elif self.keep is not None:
            self.keep([cohort])

    def reset(self) -> None:
        self.released.clear()
        self._seen.clear()


def apply_complementary_suppression(
    grid: dict[tuple[str, str], frozenset[str]],
    suppressed: dict[tuple[str, str], str],
    rows: list[str],
    cols: list[str],
    min_cell_size: int,
) -> set[tuple[str, str]]:
    """Protect every line that holds exactly one suppressed cell.

    Mutates `suppressed` in place and returns the set of line totals that must themselves be
    withheld. Repeats until stable, because suppressing a cell to protect a row can leave its
    column holding a single suppressed cell.

    A line with one suppressed cell is not protected at all: subtract the visible cells from
    the line total and the hidden value falls out. The fix is normally to suppress the
    next-smallest cell in that line. When the line has no other cell to give up -- a row with
    a single, suppressed entry -- the line's **total** is withheld instead, because there is
    nothing else for it to hide behind.
    """
    withheld_totals: set[tuple[str, str]] = set()
    changed = True
    while changed:
        changed = False
        for axis_name, axis_values, other_values, key in (
            ("row", rows, cols, lambda a, b: (a, b)),
            ("col", cols, rows, lambda a, b: (b, a)),
        ):
            for axis in axis_values:
                line = [key(axis, other) for other in other_values]
                present = [k for k in line if k in grid]
                hidden = [k for k in present if k in suppressed]
                if len(hidden) != 1:
                    continue
                visible = [k for k in present if k not in suppressed]
                if not visible:
                    withheld_totals.add((axis_name, axis))
                    continue
                victim = min(visible, key=lambda k: (len(grid[k]), k))
                suppressed[victim] = SuppressionReason.COMPLEMENTARY
                changed = True
    return withheld_totals
