"""Workforce Risk Intelligence: the fully loaded cost of discipline."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("wri-engine")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
