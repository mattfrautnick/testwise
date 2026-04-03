"""Assemble LLM context within token budget."""

from __future__ import annotations

import logging

from testwise.models import DiffResult, ParsedTestFile, RunnerConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a test selection assistant. You analyze code changes and determine \
which tests need to run.

You will receive:
1. A git diff showing what code changed
2. A list of all test files with their individual tests, annotations, and metadata
3. Optionally, the contents of some test files

Your task:
- Analyze the code changes to understand what functionality is affected
- Map changes to test files and individual tests that exercise the affected code
- Classify each test as:
  - "must_run": Tests that directly test changed code or closely related code
  - "should_run": Tests that might be affected (transitive deps, shared state, fixtures)
  - "skip": Tests clearly unrelated to the changes
- Use annotations like [covers: ...] and [tags: ...] to inform your decisions
- Provide brief reasoning for each classification
- Set confidence (0.0-1.0) reflecting your certainty
- If changes are too broad or you're unsure, set fallback_recommended=true

Guidelines:
- When in doubt, classify as "should_run" rather than "skip"
- Config/build file changes should trigger broader test selection
- Changes to shared utilities/helpers should trigger tests that import them
- New files without corresponding tests: note in summary
- Deleted files: mark their tests as "must_run" to verify nothing breaks
- Parametrized tests: if the parametrization covers the changed path, must_run

Respond with JSON matching the provided schema."""


def build_context(
    diff: DiffResult,
    parsed_files: list[ParsedTestFile],
    runners: list[RunnerConfig],
    max_context_tokens: int = 100_000,
    model: str = "anthropic/claude-sonnet-4-20250514",
) -> list[dict[str, str]]:
    """Build the messages list for the LLM call.

    Stays within the token budget using progressive truncation.
    """
    runner_map = {r.name: r for r in runners}

    # Build the test inventory section
    test_inventory = _build_test_inventory(parsed_files, runner_map)

    # Build the diff section
    diff_section = _build_diff_section(diff)

    # Estimate tokens and truncate if needed
    system_tokens = _estimate_tokens(SYSTEM_PROMPT, model)
    inventory_tokens = _estimate_tokens(test_inventory, model)
    diff_tokens = _estimate_tokens(diff_section, model)
    output_buffer = 4096  # Reserve for response

    available = max_context_tokens - system_tokens - output_buffer

    if inventory_tokens + diff_tokens > available:
        # Truncate diff first (test inventory is more important for selection)
        diff_budget = available - inventory_tokens
        if diff_budget < 500:
            # Even inventory is too large, truncate both
            inventory_budget = available // 2
            diff_budget = available - inventory_budget
            test_inventory = _truncate_text(test_inventory, inventory_budget, model)
        diff_section = _truncate_text(diff_section, diff_budget, model)

    user_content = f"## Code Changes\n\n{diff_section}\n\n## Test Inventory\n\n{test_inventory}"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _build_test_inventory(
    parsed_files: list[ParsedTestFile],
    runner_map: dict[str, RunnerConfig],
) -> str:
    """Build a structured representation of all discovered tests."""
    sections = []

    for pf in parsed_files:
        lines = [f"### {pf.file_path} ({pf.language})"]

        if pf.imports:
            lines.append(f"Imports: {', '.join(pf.imports)}")

        if pf.fixtures_used:
            lines.append(f"Fixtures: {', '.join(pf.fixtures_used)}")

        if pf.tests:
            lines.append("Tests:")
            for test in pf.tests:
                parts = [f"  - {test.name}"]

                annotations = []
                if test.tags:
                    annotations.append(f"tags: {', '.join(test.tags)}")
                if test.covers:
                    annotations.append(f"covers: {', '.join(test.covers)}")
                if test.parametrized:
                    annotations.append("parametrized")
                if annotations:
                    parts.append(f" [{'] ['.join(annotations)}]")

                if test.description:
                    # Truncate long docstrings
                    desc = test.description.split("\n")[0][:100]
                    parts.append(f' "{desc}"')

                lines.append("".join(parts))

        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _build_diff_section(diff: DiffResult) -> str:
    """Build the diff section of the context."""
    lines = [f"Base: {diff.base_ref} -> Head: {diff.head_ref}"]
    lines.append(
        f"Total: +{diff.total_additions}/-{diff.total_deletions} across {len(diff.files)} files\n"
    )

    for f in diff.files:
        lines.append(f"### {f.path} ({f.status}, +{f.additions}/-{f.deletions})")
        if f.old_path:
            lines.append(f"  Renamed from: {f.old_path}")
        if f.patch:
            lines.append(f.patch)
        lines.append("")

    return "\n".join(lines)


def _estimate_tokens(text: str, model: str) -> int:
    """Estimate token count. Uses tiktoken if available, else 4 chars/token."""
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model.split("/")[-1])
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except (ImportError, Exception):
        return len(text) // 4


def _truncate_text(text: str, max_tokens: int, model: str) -> str:
    """Truncate text to fit within a token budget."""
    current = _estimate_tokens(text, model)
    if current <= max_tokens:
        return text

    # Binary search for the right truncation point
    lines = text.splitlines(keepends=True)
    lo, hi = 0, len(lines)

    while lo < hi:
        mid = (lo + hi + 1) // 2
        truncated = "".join(lines[:mid])
        if _estimate_tokens(truncated, model) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1

    result = "".join(lines[:lo])
    if lo < len(lines):
        result += "\n[... truncated to fit context budget ...]"

    return result
