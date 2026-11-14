"""Loader for `config/generator_profile.yaml`.

Thin on purpose: the profile is read as plain nested dicts and every parameter is
documented in `src/wri_engine/generator/README.md`. The only behavior here is drawing
from the triangular distributions the profile describes.
"""

from __future__ import annotations

import random
from pathlib import Path

import yaml

from wri_engine.paths import GENERATOR_PROFILE_FILE


class GeneratorProfile:
    def __init__(self, raw: dict):
        self.raw = raw

    def __getitem__(self, key: str):
        return self.raw[key]

    def get(self, key: str, default=None):
        return self.raw.get(key, default)

    @staticmethod
    def triangular(rng: random.Random, spec: dict) -> float:
        """Draw from a triangular distribution given {min, mode, max}."""
        return rng.triangular(float(spec["min"]), float(spec["max"]), float(spec["mode"]))

    @staticmethod
    def triangular_days(rng: random.Random, spec: dict) -> int:
        return max(0, round(GeneratorProfile.triangular(rng, spec)))

    @staticmethod
    def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
        """Deterministic weighted pick. Keys are sorted so the draw never depends on
        dictionary construction order."""
        keys = sorted(weights)
        return rng.choices(keys, weights=[float(weights[k]) for k in keys], k=1)[0]


def load_generator_profile(path: Path | None = None) -> GeneratorProfile:
    path = path or GENERATOR_PROFILE_FILE
    with open(path) as fh:
        return GeneratorProfile(yaml.safe_load(fh))
