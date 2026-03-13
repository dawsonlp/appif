"""appif — CLI and service adapters for AI agent access to applications."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("appif")
except PackageNotFoundError:
    __version__ = "dev"
