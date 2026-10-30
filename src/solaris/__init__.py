"""Solaris, a mihomo desktop client."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("solaris")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"
