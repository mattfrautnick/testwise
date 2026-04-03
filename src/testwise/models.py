"""Data models for testwise."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# --- Diff Models ---


class DiffFile(BaseModel):
    """A single changed file in a git diff."""

    path: str
    status: Literal["added", "modified", "deleted", "renamed", "copied"]
    additions: int = 0
    deletions: int = 0
    patch: str = ""
    old_path: str | None = None  # For renames


class DiffResult(BaseModel):
    """Complete git diff result."""

    base_ref: str
    head_ref: str
    files: list[DiffFile] = Field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0


# --- Parser Models ---


class ParsedTest(BaseModel):
    """An individual test extracted by a parser plugin."""

    name: str
    qualified_name: str  # e.g., "tests/test_auth.py::TestAuth::test_login"
    file_path: str
    line_number: int = 0
    tags: list[str] = Field(default_factory=list)
    covers: list[str] = Field(default_factory=list)
    parametrized: bool = False
    description: str | None = None


class ParsedTestFile(BaseModel):
    """All tests parsed from a single file."""

    file_path: str
    language: str
    tests: list[ParsedTest] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    fixtures_used: list[str] = Field(default_factory=list)


# --- Test File Info (pre-parsing) ---


class TestFileInfo(BaseModel):
    """A discovered test file before parsing."""

    path: str
    language: str
    size_bytes: int = 0
    runner_name: str = ""


# --- LLM Selection Models ---


class TestClassification(str, Enum):
    """How the LLM classifies a test's relevance."""

    must_run = "must_run"
    should_run = "should_run"
    skip = "skip"


class TestSelection(BaseModel):
    """LLM's decision for a single test or test file."""

    test_id: str  # File path OR qualified test name
    granularity: Literal["file", "test"]
    classification: TestClassification
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class LLMSelectionResponse(BaseModel):
    """Structured response from the LLM."""

    summary: str
    selections: list[TestSelection]
    fallback_recommended: bool = False


# --- Config Models ---


class RunnerConfig(BaseModel):
    """Configuration for a test runner."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    test_patterns: list[str] = Field(default_factory=list)
    parser: str = "generic"
    select_mode: Literal["test", "file"] = "file"
    working_dir: str = "."
    file_arg_style: Literal["append", "flag", "none"] = "append"
    file_arg_flag: str = ""
    timeout_seconds: int = 300


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    model: str = "anthropic/claude-sonnet-4-20250514"
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_tokens: int = 4096
    temperature: float = 0.0
    timeout_seconds: int = 60
    max_context_tokens: int = 100_000


class ContextConfig(BaseModel):
    """Context assembly configuration."""

    include_test_contents: bool = True
    include_source_contents: bool = False
    max_diff_lines: int = 5000
    max_test_file_lines: int = 200


class TestwiseConfig(BaseModel):
    """Root configuration model."""

    runners: list[RunnerConfig] = Field(
        default_factory=lambda: [
            RunnerConfig(
                name="pytest",
                command="pytest",
                args=["-v", "--tb=short"],
                test_patterns=["tests/**/*.py", "test_*.py", "*_test.py"],
                parser="pytest",
                select_mode="test",
            )
        ]
    )
    llm: LLMConfig = Field(default_factory=LLMConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    fallback_on_error: bool = True
    run_should_run: bool = True
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)


# --- Result Models ---


class TestResult(BaseModel):
    """Result from running a single test or test file."""

    test_id: str
    classification: TestClassification
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    passed: bool = True


class RunReport(BaseModel):
    """Final report summarizing the entire run."""

    total_tests_discovered: int = 0
    tests_selected: int = 0
    tests_skipped: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    llm_model_used: str = ""
    llm_latency_seconds: float = 0.0
    total_duration_seconds: float = 0.0
    results: list[TestResult] = Field(default_factory=list)
    selections: list[TestSelection] = Field(default_factory=list)
    fallback_triggered: bool = False
