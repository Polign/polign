"""LangChain integration for polign_db."""

from importlib.metadata import PackageNotFoundError, version as _dist_version

from langchain_polign.vectorstores import PolignVectorStore

try:
    __version__ = _dist_version("langchain-polign")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0.1.0"

__all__ = ["PolignVectorStore", "__version__"]
