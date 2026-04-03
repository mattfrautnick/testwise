"""Test parser plugin system.

Parsers extract individual test names, annotations, and metadata from test files.
They are registered via Python entry points under the group "testwise.parsers".
"""

from __future__ import annotations

import importlib.metadata
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from testwise.models import ParsedTest, ParsedTestFile, RunnerConfig

logger = logging.getLogger(__name__)

# Registry of loaded parsers
_parsers: dict[str, BaseParser] = {}


class BaseParser(ABC):
    """Abstract base class for language-specific test parsers.

    Subclasses must implement:
    - parse_test_file: Extract tests from a file
    - build_run_command: Build the CLI command to run selected tests
    """

    name: str = ""
    languages: list[str] = []
    file_patterns: list[str] = []

    @abstractmethod
    def parse_test_file(self, file_path: Path, content: str) -> ParsedTestFile:
        """Parse a test file and extract individual tests with metadata."""
        ...

    @abstractmethod
    def build_run_command(
        self,
        tests: list[ParsedTest],
        runner_config: RunnerConfig,
        repo_root: Path,
    ) -> list[str]:
        """Build the command to run the selected tests."""
        ...


def load_parsers() -> dict[str, BaseParser]:
    """Load all registered parser plugins via entry points."""
    global _parsers

    if _parsers:
        return _parsers

    eps = importlib.metadata.entry_points()
    if hasattr(eps, "select"):
        parser_eps = eps.select(group="testwise.parsers")
    else:
        parser_eps = eps.get("testwise.parsers", [])  # type: ignore[arg-type]

    for ep in parser_eps:
        try:
            cls = ep.load()
            instance = cls()
            _parsers[ep.name] = instance
            logger.debug("Loaded parser plugin: %s", ep.name)
        except Exception:
            logger.warning("Failed to load parser plugin: %s", ep.name, exc_info=True)

    return _parsers


def get_parser(name: str) -> BaseParser | None:
    """Get a parser by name, loading plugins if needed."""
    parsers = load_parsers()
    return parsers.get(name)
