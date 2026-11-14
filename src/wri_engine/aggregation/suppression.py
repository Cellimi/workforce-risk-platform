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

Limits, stated plainly
----------------------
The ledger is a pairwise residual check, not a full query audit. It cannot see combinations
of three or more earlier answers, and it is per-ledger: a caller who starts a fresh ledger
starts fresh. It is scoped per role and per session in the API. Full disclosure auditing is
not Phase 1 work, and the demo should not claim otherwise.
"""

from __future__ import annotations

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

    def would_expose(self, cohort: frozenset[str]) -> bool:
        """True when releasing `cohort` would let a caller isolate a group that is too small.

        A caller who holds the total for cohort L and is then given the total for cohort C
        can compute the totals for `C - L` and `L - C` by subtraction whenever one contains
        the other. Either residual being non-empty and under the threshold is a disclosure.
        """
        if not cohort:
            return False
        for prior in self.released:
            for residual in (cohort - prior, prior - cohort):
                if 0 < len(residual) < self.min_cell_size:
                    return True
        return False

    def record(self, cohort: frozenset[str]) -> None:
        if cohort:
            self.released.append(cohort)

    def reset(self) -> None:
        self.released.clear()


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
