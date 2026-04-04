"""CLI entry point and orchestration."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import click

from testwise.config import get_repo_root, load_config
from testwise.context_builder import build_context
from testwise.diff_analyzer import filter_diff_files, get_diff, truncate_diff
from testwise.exceptions import LLMError, TestwiseError
from testwise.llm_selector import fallback_all_tests, select_tests
from testwise.models import RunReport, TestClassification
from testwise.reporter import report_results
from testwise.test_discovery import discover_tests, parse_test_files
from testwise.test_runner import run_selected_tests


@click.command()
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    help="Path to .testwise.yml",
)
@click.option("--base-ref", "-b", help="Base git ref to diff against")
@click.option("--head-ref", help="Head git ref (default: HEAD)")
@click.option(
    "--output",
    "-o",
    "output_format",
    type=click.Choice(["text", "json", "github"]),
    default="text",
    help="Output format",
)
@click.option("--output-file", type=click.Path(path_type=Path), help="Write JSON report to file")
@click.option("--dry-run", is_flag=True, help="Show selections without running tests")
@click.option("--fallback", is_flag=True, help="Skip LLM, run all tests")
@click.option(
    "--run-level",
    type=click.Choice(["must_run", "should_run", "all"]),
    default="should_run",
    help="Minimum classification to execute",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.version_option(package_name="testwiseai")
def main(
    config_path: Path | None,
    base_ref: str | None,
    head_ref: str | None,
    output_format: str,
    output_file: Path | None,
    dry_run: bool,
    fallback: bool,
    run_level: str,
    verbose: bool,
) -> None:
    """Testwise - LLM-powered test selection for CI/CD pipelines."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(name)s: %(message)s",
    )

    start_time = time.monotonic()

    try:
        # 1. Load config
        config = load_config(config_path)

        # 2. Get repo root
        repo_root = get_repo_root()

        # 3. Get diff
        diff = get_diff(base_ref=base_ref, head_ref=head_ref, repo_path=repo_root)

        if not diff.files:
            click.echo("No changes detected.")
            sys.exit(0)

        # Filter diff files
        diff.files = filter_diff_files(
            diff.files,
            include=config.include_patterns,
            exclude=config.exclude_patterns,
        )

        # Truncate if needed
        diff = truncate_diff(diff, config.context.max_diff_lines)

        click.echo(
            f"Changes: {len(diff.files)} files (+{diff.total_additions}/-{diff.total_deletions})"
        )

        # 4. Discover and parse test files
        test_files = discover_tests(repo_root, config.runners)
        if not test_files:
            click.echo("No test files found. Check your runner patterns in .testwise.yml", err=True)
            sys.exit(2)

        parsed_files = parse_test_files(test_files, config.runners, repo_root)
        total_tests = sum(len(pf.tests) for pf in parsed_files)
        click.echo(f"Discovered: {len(test_files)} test files, {total_tests} individual tests")

        # 5. Get test selections
        llm_latency = 0.0
        fallback_triggered = False

        if fallback:
            # User forced fallback
            llm_response = fallback_all_tests(parsed_files, "User requested --fallback")
            fallback_triggered = True
        else:
            # Build context and call LLM
            messages = build_context(
                diff=diff,
                parsed_files=parsed_files,
                runners=config.runners,
                max_context_tokens=config.llm.max_context_tokens,
                model=config.llm.model,
            )

            try:
                llm_response, llm_latency = select_tests(messages, config.llm)

                if llm_response.fallback_recommended:
                    click.echo("LLM recommended fallback — running all tests")
                    llm_response = fallback_all_tests(parsed_files, "LLM recommended fallback")
                    fallback_triggered = True

            except LLMError as e:
                click.echo(f"LLM error: {e}", err=True)
                if config.fallback_on_error:
                    click.echo("Falling back to running all tests")
                    llm_response = fallback_all_tests(parsed_files, str(e))
                    fallback_triggered = True
                else:
                    sys.exit(2)

        # 6. Filter by run level
        min_classifications = {
            "must_run": {TestClassification.must_run},
            "should_run": {TestClassification.must_run, TestClassification.should_run},
            "all": {
                TestClassification.must_run,
                TestClassification.should_run,
                TestClassification.skip,
            },
        }
        allowed = min_classifications[run_level]

        active_selections = [s for s in llm_response.selections if s.classification in allowed]
        skipped_selections = [s for s in llm_response.selections if s.classification not in allowed]

        click.echo(f"Selected: {len(active_selections)} tests (skipping {len(skipped_selections)})")

        # 7. Execute tests (unless dry run)
        results = []
        if not dry_run and active_selections:
            click.echo("Running selected tests...")
            results = run_selected_tests(
                selections=active_selections,
                parsed_files=parsed_files,
                runners=config.runners,
                repo_root=repo_root,
            )

        # 8. Build report
        total_duration = time.monotonic() - start_time
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)

        report = RunReport(
            total_tests_discovered=total_tests,
            tests_selected=len(active_selections),
            tests_skipped=len(skipped_selections),
            tests_passed=passed,
            tests_failed=failed,
            llm_model_used=config.llm.model,
            llm_latency_seconds=llm_latency,
            total_duration_seconds=total_duration,
            results=results,
            selections=llm_response.selections,
            fallback_triggered=fallback_triggered,
        )

        # 9. Report
        report_results(report, output_format, output_file)

        # 10. Exit code
        if failed > 0:
            sys.exit(1)

    except TestwiseError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
