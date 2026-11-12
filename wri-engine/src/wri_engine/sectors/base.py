"""What a sector pack is.

The engine costs a discipline action the same way everywhere: a component looks at a record
and, if the record shows the event happened, produces a line item. What changes between a
county and a company is not the arithmetic but three things:

* **which components can apply** -- an unemployment claim or a liability policy is a private
  employer's problem, not a county's;
* **which benefits multiplier loads a wage** -- state and local government runs well above
  private industry;
* **which coefficient ids the engine must find before it starts**, so a missing one fails at
  load time with a name rather than halfway through a run.

A pack holds exactly that and nothing else. It holds no arithmetic: if you find yourself
wanting to put a number here, it belongs in `config/assumptions.yaml` (rule 1).

The packs deliberately share one coefficient namespace rather than each having its own
prefixed copy. Where the two sectors genuinely do the same thing -- an HR officer's hours on
a written reprimand -- they share the id and the value, and there is one number to confirm
instead of two. Only a genuine difference earns a new id.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from wri_engine.schema import AppealForum


@dataclass(frozen=True)
class SectorPack:
    """One sector's view of the cost model."""

    id: str
    label: str
    description: str
    #: Component modules, in the order their line items are produced.
    components: tuple[ModuleType, ...]
    #: `Employee.benefits_group` -> the assumption id holding that group's multiplier.
    benefits_assumption_by_group: dict[str, str]
    #: The benefits group this sector's intake assigns when the data does not say.
    default_benefits_group: str
    #: Turnover profiles whose C5 coefficients must exist for this sector.
    turnover_profiles: tuple[str, ...]
    #: Challenge routes this sector's data can contain, for the C4 preflight.
    challenge_forums: tuple[AppealForum, ...]

    def component_codes(self) -> tuple[str, ...]:
        """The component codes this pack can produce, for documentation and the UI."""
        codes: list[str] = []
        for module in self.components:
            for code in getattr(module, "PRODUCES", ()):
                if code not in codes:
                    codes.append(code)
        return tuple(codes)
