"""Test file discovery and parser routing."""

from __future__ import annotations

import logging
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from testwise.models import ParsedTestFile, RunnerConfig, TestFileInfo
from testwise.parsers import get_parser

logger = logging.getLogger(__name__)


def discover_tests(
    repo_root: Path,
    runners: list[RunnerConfig],
) -> list[TestFileInfo]:
    """Find all test files in the repository matching runner patterns.

    Uses `git ls-files` to respect .gitignore.
    """
    tracked_files = _get_tracked_files(repo_root)
    test_files: list[TestFileInfo] = []
    seen: set[str] = set()

    for runner in runners:
        for file_path in tracked_files:
            if file_path in seen:
                continue
            if _matches_patterns(file_path, runner.test_patterns):
                full_path = repo_root / file_path
                size = full_path.stat().st_size if full_path.exists() else 0
                language = _detect_language(file_path)
                test_files.append(
                    TestFileInfo(
                        path=file_path,
                        language=language,
                        size_bytes=size,
                        runner_name=runner.name,
                    )
                )
                seen.add(file_path)

    logger.info("Discovered %d test files across %d runners", len(test_files), len(runners))
    return test_files


def parse_test_files(
    test_files: list[TestFileInfo],
    runners: list[RunnerConfig],
    repo_root: Path,
) -> list[ParsedTestFile]:
    """Parse test files using the appropriate parser plugin for each runner.

    Returns ParsedTestFile objects with test-level detail when a parser
    is available, or file-level entries via the generic parser.
    """
    runner_map = {r.name: r for r in runners}
    parsed: list[ParsedTestFile] = []

    for tf in test_files:
        runner = runner_map.get(tf.runner_name)
        if not runner:
            continue

        parser = get_parser(runner.parser)
        if parser is None:
            parser = get_parser("generic")
        if parser is None:
            logger.warning("No parser available for runner %s, skipping %s", runner.name, tf.path)
            continue

        full_path = repo_root / tf.path
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            parsed_file = parser.parse_test_file(Path(tf.path), content)
            parsed.append(parsed_file)
        except Exception:
            logger.warning("Failed to parse %s", tf.path, exc_info=True)
            # Fall back to file-level entry
            generic = get_parser("generic")
            if generic:
                parsed.append(generic.parse_test_file(Path(tf.path), ""))

    logger.info(
        "Parsed %d test files, total %d individual tests",
        len(parsed),
        sum(len(pf.tests) for pf in parsed),
    )
    return parsed


def _get_tracked_files(repo_root: Path) -> list[str]:
    """Get all git-tracked files."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            logger.warning("git ls-files failed, falling back to filesystem walk")
            return _walk_files(repo_root)
        return [line for line in result.stdout.strip().splitlines() if line]
    except FileNotFoundError:
        return _walk_files(repo_root)


def _walk_files(repo_root: Path) -> list[str]:
    """Fallback: walk the filesystem (respects common ignore patterns)."""
    ignore_dirs = {
        ".git",
        "node_modules",
        "__pycache__",
        ".tox",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
    }
    files = []
    for path in repo_root.rglob("*"):
        if path.is_file() and not any(d in path.parts for d in ignore_dirs):
            files.append(str(path.relative_to(repo_root)))
    return files


def _matches_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the given glob patterns."""
    for pattern in patterns:
        if fnmatch(file_path, pattern):
            return True
        # Also try matching just the filename
        if fnmatch(Path(file_path).name, pattern):
            return True
    return False


_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".swift": "swift",
    ".kt": "kotlin",
}


def _detect_language(file_path: str) -> str:
    suffix = Path(file_path).suffix
    return _LANGUAGE_MAP.get(suffix, suffix.lstrip(".") or "unknown")
