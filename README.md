<p align="center">
  <h1 align="center">Testwise</h1>
  <p align="center">
    LLM-powered test selection for CI/CD pipelines
    <br />
    <em>Run only the tests that matter. Save CI time without sacrificing coverage.</em>
  </p>
  <p align="center">
    <a href="https://github.com/mattfrautnick/testwise/actions/workflows/ci.yml"><img src="https://github.com/mattfrautnick/testwise/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://pypi.org/project/testwise/"><img src="https://img.shields.io/pypi/v/testwise.svg" alt="PyPI"></a>
    <a href="https://pypi.org/project/testwise/"><img src="https://img.shields.io/pypi/pyversions/testwise.svg" alt="Python"></a>
    <a href="https://github.com/mattfrautnick/testwise/blob/main/LICENSE"><img src="https://img.shields.io/github/license/mattfrautnick/testwise" alt="License"></a>
  </p>
</p>

---

Testwise analyzes your git diff and uses an LLM to classify every test as `must_run`, `should_run`, or `skip` — then executes only what's needed. It supports **test-level granularity** for languages with parser plugins and falls back to file-level selection for everything else.

## Why Testwise?

Large test suites slow down CI. Most changes only affect a fraction of your tests, but running the full suite every time wastes minutes (or hours). Existing static-analysis approaches miss indirect dependencies and cross-cutting concerns. Testwise uses an LLM that actually understands your code changes and test structure to make smarter decisions — with a safe fallback to run everything if it's ever uncertain.

## How It Works

```
git diff ─> Discover Tests ─> Parse with Plugins ─> LLM Classifies ─> Run Selected ─> Report
```

1. **Diff Analysis** — Extracts the git diff between base and head refs
2. **Test Discovery** — Finds all test files and parses individual test functions via parser plugins
3. **LLM Classification** — Sends diff + test inventory to an LLM with structured output
4. **Selective Execution** — Runs only selected tests and reports results with GitHub annotations

## Features

- **Hybrid Granularity** — Test-level selection for languages with parser plugins (pytest built-in), file-level fallback for others
- **Plugin Architecture** — Extensible parser system via Python entry points. [Write a parser](#writing-a-parser-plugin) for any test framework.
- **Any LLM Provider** — Uses [litellm](https://github.com/BerriAI/litellm) to support Claude, GPT, Gemini, and 100+ other models
- **GitHub Actions** — Ships as a composite action with step summary, annotations, and outputs
- **Safe Fallback** — If the LLM fails or is uncertain, falls back to running all tests
- **Test Annotations** — Supports `@pytest.mark.covers()` to explicitly map tests to code areas

## Quick Start

### Install

```bash
pip install testwise
```

### Configure

Create `.testwise.yml` in your repo root:

```yaml
runners:
  - name: pytest
    command: pytest
    args: ["-v", "--tb=short"]
    test_patterns: ["tests/**/*.py", "test_*.py"]
    parser: pytest
    select_mode: test

llm:
  model: anthropic/claude-sonnet-4-20250514
  api_key_env: ANTHROPIC_API_KEY
```

### Run

```bash
# Dry run — see what the LLM would select
testwise --dry-run

# Run selected tests
testwise

# Force all tests (bypass LLM)
testwise --fallback
```

### GitHub Actions

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history needed for diff

      - uses: mattfrautnick/testwise@v1
        with:
          api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          run-level: should_run
```

The action writes a Markdown summary to `$GITHUB_STEP_SUMMARY` and emits `::error::` annotations for failing tests inline in your PR diff.

## Test Annotations

Testwise's pytest parser understands standard markers and a custom `@covers` annotation that explicitly maps tests to code areas:

```python
import pytest

@pytest.mark.covers("auth_module", "user.login")
def test_login_success(client, db):
    """Verify successful login flow."""
    ...

@pytest.mark.integration
@pytest.mark.covers("payment_service")
def test_checkout_flow(client):
    ...

@pytest.mark.parametrize("role", ["admin", "user", "guest"])
def test_permissions(role):
    ...
```

The parser also extracts imports and fixture references automatically — no annotation required for basic dependency mapping.

## Parser Plugins

Testwise uses a plugin architecture for language-specific test parsing. Plugins are registered via Python entry points.

### Built-in Parsers

| Parser | Language | Granularity | Features |
|--------|----------|-------------|----------|
| `pytest` | Python | Test-level | Markers, covers, parametrize, fixtures, imports |
| `generic` | Any | File-level | Fallback for unsupported languages |

### Writing a Parser Plugin

Implement `BaseParser` and register it as an entry point:

```python
from testwise.parsers import BaseParser
from testwise.models import ParsedTest, ParsedTestFile, RunnerConfig
from pathlib import Path

class JestParser(BaseParser):
    name = "jest"
    languages = ["javascript", "typescript"]
    file_patterns = ["*.test.ts", "*.test.js", "*.spec.ts", "*.spec.js"]

    def parse_test_file(self, file_path: Path, content: str) -> ParsedTestFile:
        # Parse describe/it blocks, extract test names
        ...

    def build_run_command(self, tests, runner_config, repo_root):
        # Build jest --testNamePattern command
        ...
```

```toml
# pyproject.toml
[project.entry-points."testwise.parsers"]
jest = "my_package.jest_parser:JestParser"
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for a full guide on writing and testing parser plugins.

## CLI Reference

```
testwise [OPTIONS]

Options:
  -c, --config PATH                    Path to .testwise.yml
  -b, --base-ref TEXT                  Base git ref to diff against
      --head-ref TEXT                  Head git ref (default: HEAD)
  -o, --output [text|json|github]      Output format (default: text)
      --output-file PATH               Write JSON report to file
      --dry-run                        Show selections without running tests
      --fallback                       Skip LLM, run all tests
      --run-level [must_run|should_run|all]  Minimum classification to run
  -v, --verbose                        Verbose logging
      --version                        Show version
      --help                           Show this message
```

## Configuration Reference

See [`.testwise.example.yml`](.testwise.example.yml) for a fully commented example.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `runners[].name` | string | required | Runner identifier |
| `runners[].command` | string | required | Test runner command |
| `runners[].args` | list | `[]` | Additional arguments |
| `runners[].test_patterns` | list | `[]` | Glob patterns for test files |
| `runners[].parser` | string | `"generic"` | Parser plugin name |
| `runners[].select_mode` | string | `"file"` | `"test"` or `"file"` |
| `runners[].timeout_seconds` | int | `300` | Per-runner timeout |
| `llm.model` | string | `"anthropic/claude-sonnet-4-20250514"` | LLM model ([litellm format](https://docs.litellm.ai/docs/providers)) |
| `llm.api_key_env` | string | `"ANTHROPIC_API_KEY"` | Env var containing API key |
| `llm.max_context_tokens` | int | `100000` | Token budget for context |
| `llm.temperature` | float | `0.0` | LLM temperature |
| `fallback_on_error` | bool | `true` | Run all tests if LLM fails |
| `run_should_run` | bool | `true` | Also run "should_run" tests |

## Roadmap

Testwise is in early development. Here's what's planned:

- [ ] Jest/Vitest parser plugin
- [ ] Go test parser plugin
- [ ] Caching layer — skip LLM call for identical diffs
- [ ] Cost tracking — log token usage and estimated cost per run
- [ ] Confidence threshold — auto-fallback below a configurable confidence
- [ ] Test impact analysis — learn from historical runs which tests fail for which changes
- [ ] GitLab CI integration

Have an idea? [Open an issue](https://github.com/mattfrautnick/testwise/issues) or [start a discussion](https://github.com/mattfrautnick/testwise/discussions).

## Contributing

Contributions are welcome! Whether it's a bug fix, a new parser plugin, or documentation improvements — all contributions help.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, architecture overview, and the full guide to writing parser plugins.

## Community

- [GitHub Issues](https://github.com/mattfrautnick/testwise/issues) — Bug reports and feature requests
- [GitHub Discussions](https://github.com/mattfrautnick/testwise/discussions) — Questions, ideas, and show & tell

## License

[MIT](LICENSE)
