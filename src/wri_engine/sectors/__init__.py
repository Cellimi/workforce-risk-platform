"""Sector packs: which cost model an engagement runs.

An engagement already records its sector. This is where that string becomes a decision about
which components run, which benefits multiplier loads a wage, and which coefficient ids must
exist before the engine starts.

Ask for a pack by id. Asking for one that is not installed is a hard error naming what is,
for the same reason a missing assumption id is: a cost model that quietly falls back to the
wrong sector is worse than one that refuses to start.
"""

from __future__ import annotations

from wri_engine.sectors import private_sector, public_sector
from wri_engine.sectors.base import SectorPack

PACKS: dict[str, SectorPack] = {
    public_sector.PACK.id: public_sector.PACK,
    private_sector.PACK.id: private_sector.PACK,
}

#: The sector assumed when a caller does not name one. Everything built before sectors
#: existed is public sector, so this keeps that behaviour exactly.
DEFAULT_SECTOR = public_sector.PACK.id


class UnknownSectorError(KeyError):
    """Raised when a sector has no installed cost model."""


def get_pack(sector_id: str | None = None) -> SectorPack:
    pack = PACKS.get(sector_id or DEFAULT_SECTOR)
    if pack is None:
        raise UnknownSectorError(
            f"no cost model is installed for sector {sector_id!r}. "
            f"Installed: {', '.join(sorted(PACKS))}"
        )
    return pack


def sector_ids() -> tuple[str, ...]:
    return tuple(sorted(PACKS))


__all__ = ["DEFAULT_SECTOR", "PACKS", "SectorPack", "UnknownSectorError", "get_pack", "sector_ids"]
