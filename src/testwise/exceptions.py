"""Custom exceptions for testwise."""


class TestwiseError(Exception):
    """Base exception for all testwise errors."""


class ConfigError(TestwiseError):
    """Invalid or missing configuration."""


class DiffError(TestwiseError):
    """Git diff extraction failure."""


class LLMError(TestwiseError):
    """Base exception for LLM-related errors."""


class LLMTimeoutError(LLMError):
    """LLM call timed out."""


class LLMParseError(LLMError):
    """Failed to parse LLM response."""


class TestRunnerError(TestwiseError):
    """Test execution failure."""


class ContextBudgetExceededError(TestwiseError):
    """Cannot fit required content within the token budget."""
