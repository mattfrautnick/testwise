"""Execute selected tests via subprocess."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from testwise.models import (
    ParsedTest,
    ParsedTestFile,
    RunnerConfig,
    TestClassification,
    TestResult,
    TestSelection,
)
from testwise.parsers import get_parser

logger = logging.getLogger(__name__)


def run_selected_tests(
    selections: list[TestSelection],
    parsed_files: list[ParsedTestFile],
    runners: list[RunnerConfig],
    repo_root: Path,
) -> list[TestResult]:
    """Execute the selected tests grouped by runner.

    Groups selected tests by their runner, then invokes each runner once
    with the filtered test list.
    """
    runner_map = {r.name: r for r in runners}
    file_map = {pf.file_path: pf for pf in parsed_files}

    # Build a map from test_id to (runner, ParsedTest)
    test_to_runner: dict[str, tuple[RunnerConfig, ParsedTest]] = {}
    for pf in parsed_files:
        runner = _find_runner_for_file(pf.file_path, runners)
        if runner:
            for test in pf.tests:
                test_to_runner[test.qualified_name] = (runner, test)
                # Also map file path for file-level selections
                test_to_runner[pf.file_path] = (runner, test)

    # Group selected tests by runner
    runner_tests: dict[str, list[ParsedTest]] = {}
    selected_ids: set[str] = set()

    for sel in selections:
        if sel.classification == TestClassification.skip:
            continue

        if sel.test_id in test_to_runner:
            runner, test = test_to_runner[sel.test_id]
            runner_tests.setdefault(runner.name, []).append(test)
            selected_ids.add(sel.test_id)
        elif sel.granularity == "file":
            # File-level selection — include all tests in the file
            matched_pf = file_map.get(sel.test_id)
            if matched_pf:
                pf = matched_pf
                runner = _find_runner_for_file(pf.file_path, runners)
                if runner:
                    for test in pf.tests:
                        if test.qualified_name not in selected_ids:
                            runner_tests.setdefault(runner.name, []).append(test)
                            selected_ids.add(test.qualified_name)

    if not runner_tests:
        logger.info("No tests selected for execution")
        return []

    # Execute each runner group
    results: list[TestResult] = []
    for runner_name, tests in runner_tests.items():
        runner = runner_map.get(runner_name)
        if not runner:
            continue

        result = _execute_runner(runner, tests, repo_root, selections)
        results.extend(result)

    return results


def _execute_runner(
    runner: RunnerConfig,
    tests: list[ParsedTest],
    repo_root: Path,
    selections: list[TestSelection],
) -> list[TestResult]:
    """Execute a single runner with the given tests."""
    parser = get_parser(runner.parser)
    if parser is None:
        parser = get_parser("generic")
    if parser is None:
        logger.error("No parser available for runner %s", runner.name)
        return []

    cmd = parser.build_run_command(tests, runner, repo_root)
    working_dir = repo_root / runner.working_dir

    logger.info("Running: %s (in %s)", " ".join(cmd), working_dir)

    selection_map = {s.test_id: s for s in selections}

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=runner.timeout_seconds,
            cwd=str(working_dir),
        )
        duration = time.monotonic() - start

        passed = result.returncode == 0

        # Create a result for each test in the group
        results = []
        for test in tests:
            sel = selection_map.get(test.qualified_name) or selection_map.get(test.file_path)
            classification = sel.classification if sel else TestClassification.must_run

            results.append(
                TestResult(
                    test_id=test.qualified_name,
                    classification=classification,
                    exit_code=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    duration_seconds=duration,
                    passed=passed,
                )
            )

        if not passed:
            logger.warning(
                "Runner %s failed (exit %d): %s",
                runner.name,
                result.returncode,
                result.stderr[:500] if result.stderr else result.stdout[:500],
            )

        return results

    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        logger.error("Runner %s timed out after %ds", runner.name, runner.timeout_seconds)
        return [
            TestResult(
                test_id=test.qualified_name,
                classification=TestClassification.must_run,
                exit_code=-1,
                stderr=f"Timed out after {runner.timeout_seconds}s",
                duration_seconds=duration,
                passed=False,
            )
            for test in tests
        ]

    except FileNotFoundError:
        logger.error("Runner command not found: %s", runner.command)
        return [
            TestResult(
                test_id=test.qualified_name,
                classification=TestClassification.must_run,
                exit_code=-1,
                stderr=f"Runner '{runner.command}' not found. Is it installed?",
                duration_seconds=0.0,
                passed=False,
            )
            for test in tests
        ]


def _find_runner_for_file(file_path: str, runners: list[RunnerConfig]) -> RunnerConfig | None:
    """Find the first runner whose patterns match a file path."""
    from fnmatch import fnmatch

    for runner in runners:
        for pattern in runner.test_patterns:
            if fnmatch(file_path, pattern) or fnmatch(Path(file_path).name, pattern):
                return runner
    return None
