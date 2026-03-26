"""git-study package."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("git-study")
except PackageNotFoundError:
    __version__ = "0.1.6"
