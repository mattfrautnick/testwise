"""Format and output test results."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from testwise.models import RunReport, TestClassification

logger = logging.getLogger(__name__)


def report_results(
    report: RunReport,
    output_format: str = "text",
    output_file: Path | None = None,
) -> None:
    """Report results in the specified format."""
    if output_format == "github":
        _write_github_summary(report)
        _write_github_outputs(report)
        _write_github_annotations(report)
        # Also print text for logs
        print(_format_text_report(report))
    elif output_format == "json":
        json_str = report.model_dump_json(indent=2)
        print(json_str)
    else:
        print(_format_text_report(report))

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(report.model_dump_json(indent=2))
        logger.info("Report written to %s", output_file)


def _format_text_report(report: RunReport) -> str:
    """Human-readable terminal output."""
    lines = [
        "",
        "=" * 60,
        "  Testwise Results",
        "=" * 60,
        "",
        f"  Model:    {report.llm_model_used}",
        f"  LLM time: {report.llm_latency_seconds:.1f}s",
        f"  Total:    {report.total_duration_seconds:.1f}s",
        "",
        f"  Discovered: {report.total_tests_discovered} tests",
        f"  Selected:   {report.tests_selected} "
        f"({_count_by_class(report, TestClassification.must_run)} must_run, "
        f"{_count_by_class(report, TestClassification.should_run)} should_run)",
        f"  Skipped:    {report.tests_skipped}",
        "",
    ]

    if report.fallback_triggered:
        lines.append("  ** FALLBACK: Running all tests **")
        lines.append("")

    if report.results:
        passed = sum(1 for r in report.results if r.passed)
        failed = sum(1 for r in report.results if not r.passed)
        lines.append(f"  Results: {passed} passed, {failed} failed")
        lines.append("")

        # Show failures
        failures = [r for r in report.results if not r.passed]
        if failures:
            lines.append("  FAILED:")
            for r in failures:
                lines.append(f"    {r.test_id} (exit {r.exit_code})")
                if r.stderr:
                    for err_line in r.stderr.strip().splitlines()[:5]:
                        lines.append(f"      {err_line}")
            lines.append("")

    # Show selections with reasoning
    if report.selections:
        lines.append("  SELECTIONS:")
        for sel in report.selections[:50]:  # Limit display
            tag = sel.classification.value
            lines.append(f"    [{tag:10s}] {sel.test_id}")
            if sel.reasoning:
                lines.append(f"               {sel.reasoning[:80]}")

        if len(report.selections) > 50:
            lines.append(f"    ... and {len(report.selections) - 50} more")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


def _write_github_summary(report: RunReport) -> None:
    """Write markdown to $GITHUB_STEP_SUMMARY."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    passed = sum(1 for r in report.results if r.passed)
    failed = sum(1 for r in report.results if not r.passed)
    savings = (
        f"{report.tests_skipped}/{report.total_tests_discovered} tests skipped "
        f"({_pct(report.tests_skipped, report.total_tests_discovered)})"
    )

    lines = [
        "## Testwise Results",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Model | `{report.llm_model_used}` |",
        f"| LLM Latency | {report.llm_latency_seconds:.1f}s |",
        f"| Tests Discovered | {report.total_tests_discovered} |",
        f"| Tests Selected | {report.tests_selected} |",
        f"| Tests Skipped | {report.tests_skipped} |",
        f"| Passed | {passed} |",
        f"| Failed | {failed} |",
        f"| Savings | {savings} |",
        "",
    ]

    if report.fallback_triggered:
        lines.append("> **Warning**: Fallback triggered — all tests were run.")
        lines.append("")

    # Expandable details
    if report.selections:
        lines.append("<details>")
        lines.append("<summary>Selection Details</summary>")
        lines.append("")
        lines.append("| Test | Classification | Reasoning |")
        lines.append("|------|----------------|-----------|")
        for sel in report.selections[:100]:
            lines.append(f"| `{sel.test_id}` | {sel.classification.value} | {sel.reasoning[:60]} |")
        if len(report.selections) > 100:
            lines.append(f"| ... | {len(report.selections) - 100} more | |")
        lines.append("")
        lines.append("</details>")

    try:
        with open(summary_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        logger.warning("Failed to write GitHub step summary")


def _write_github_outputs(report: RunReport) -> None:
    """Write key-value pairs to $GITHUB_OUTPUT."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    outputs = {
        "tests_run": str(len(report.results)),
        "tests_passed": str(report.tests_passed),
        "tests_failed": str(report.tests_failed),
        "tests_skipped": str(report.tests_skipped),
        "llm_model": report.llm_model_used,
        "fallback_triggered": str(report.fallback_triggered).lower(),
    }

    try:
        with open(output_path, "a") as f:
            for key, value in outputs.items():
                f.write(f"{key}={value}\n")
    except OSError:
        logger.warning("Failed to write GitHub outputs")


def _write_github_annotations(report: RunReport) -> None:
    """Emit GitHub Actions workflow commands for annotations."""
    failures = [r for r in report.results if not r.passed]
    for r in failures:
        # Extract file path from test_id (e.g., "tests/test_auth.py::TestAuth::test_login")
        file_path = r.test_id.split("::")[0] if "::" in r.test_id else r.test_id
        msg = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "Test failed"
        print(f"::error file={file_path}::{msg}")

    if report.tests_selected > 0:
        print(
            f"::notice::Testwise: "
            f"{report.tests_selected}/{report.total_tests_discovered} "
            f"tests selected, saved "
            f"~{_pct(report.tests_skipped, report.total_tests_discovered)}"
            f" of test time"
        )


def _count_by_class(report: RunReport, classification: TestClassification) -> int:
    return sum(1 for s in report.selections if s.classification == classification)


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{part * 100 // total}%"
