# Contributing to Testwise

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/mattfrautnick/testwise.git
cd testwise
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
pytest --cov=testwise --cov-report=term-missing
```

## Linting

```bash
ruff check src/ tests/
ruff format src/ tests/
mypy src/testwise/
```

## Project Structure

```
src/testwise/
  cli.py              # CLI entry point + orchestration
  models.py           # All Pydantic data models
  config.py           # Configuration loading
  diff_analyzer.py    # Git diff extraction
  test_discovery.py   # Test file discovery
  context_builder.py  # LLM context assembly
  llm_selector.py     # LLM interaction
  test_runner.py      # Test execution
  reporter.py         # Results formatting
  parsers/            # Parser plugin system
    __init__.py       # BaseParser ABC + registry
    pytest_parser.py  # Python/pytest parser
    generic_parser.py # File-level fallback
```

## Writing a Parser Plugin

Parser plugins enable test-level selection for new languages/frameworks.

### 1. Create the Parser

Implement `BaseParser`:

```python
from testwise.parsers import BaseParser
from testwise.models import ParsedTest, ParsedTestFile, RunnerConfig
from pathlib import Path

class MyParser(BaseParser):
    name = "myframework"
    languages = ["javascript"]
    file_patterns = ["*.test.js"]

    def parse_test_file(self, file_path: Path, content: str) -> ParsedTestFile:
        """Parse a test file and extract individual tests."""
        tests = []
        # Your parsing logic here - extract test names, tags, etc.
        return ParsedTestFile(
            file_path=str(file_path),
            language="javascript",
            tests=tests,
            imports=[],          # Module imports for dependency mapping
            fixtures_used=[],    # Shared setup/fixtures
        )

    def build_run_command(
        self,
        tests: list[ParsedTest],
        runner_config: RunnerConfig,
        repo_root: Path,
    ) -> list[str]:
        """Build CLI command to run specific tests."""
        cmd = [runner_config.command, *runner_config.args]
        # Add test selection flags specific to your framework
        return cmd
```

### 2. Register via Entry Points

In your package's `pyproject.toml`:

```toml
[project.entry-points."testwise.parsers"]
myframework = "my_package.parser:MyParser"
```

### 3. Test Your Parser

Write tests that verify:
- Test functions are correctly extracted from sample files
- Tags/annotations are properly parsed
- The `build_run_command` produces valid CLI commands
- Edge cases: empty files, syntax errors, nested classes

### What Makes a Good Parser

- **Extract test names** with qualified paths (file::class::test)
- **Parse annotations/decorators** into tags and covers lists
- **Detect parametrized tests** (the LLM uses this info)
- **Extract imports** (helps the LLM map code dependencies)
- **Handle errors gracefully** (syntax errors should fall back to file-level)

## Pull Request Guidelines

1. Fork and create a feature branch
2. Write tests for new functionality
3. Ensure all tests pass: `pytest`
4. Ensure linting passes: `ruff check src/ tests/`
5. Keep commits focused and well-described
6. Open a PR with a clear description of what and why

## Reporting Issues

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your testwise version (`testwise --version`)
- Your config file (redact API keys)
