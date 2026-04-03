"""LLM interaction for test selection."""

from __future__ import annotations

import json
import logging
import os
import time

import litellm

from testwise.exceptions import LLMError, LLMParseError, LLMTimeoutError
from testwise.models import (
    LLMConfig,
    LLMSelectionResponse,
    ParsedTestFile,
    TestClassification,
    TestSelection,
)

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True


def select_tests(
    messages: list[dict[str, str]],
    config: LLMConfig,
) -> tuple[LLMSelectionResponse, float]:
    """Call the LLM to classify tests. Returns (response, latency_seconds).

    Three-tier fallback:
    1. Structured output via response_format
    2. Manual JSON extraction from text
    3. Raises LLMError (caller decides whether to fallback to all tests)
    """
    # Set API key from configured env var
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        # Try common fallbacks
        for key_name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            api_key = os.environ.get(key_name)
            if api_key:
                break

    if not api_key:
        raise LLMError(f"No API key found. Set {config.api_key_env} environment variable.")

    response_schema = LLMSelectionResponse.model_json_schema()

    start = time.monotonic()

    # Tier 1: Try structured output
    try:
        response = _call_with_structured_output(messages, config, api_key, response_schema)
        latency = time.monotonic() - start
        logger.info("LLM responded in %.1fs (structured output)", latency)
        return response, latency
    except LLMParseError:
        raise
    except LLMError as e:
        logger.info("Structured output not supported, falling back to text mode: %s", e)

    # Tier 2: Try text mode with JSON in prompt
    try:
        response = _call_with_text_mode(messages, config, api_key, response_schema)
        latency = time.monotonic() - start
        logger.info("LLM responded in %.1fs (text mode)", latency)
        return response, latency
    except Exception as e:
        latency = time.monotonic() - start
        raise LLMError(f"LLM call failed after {latency:.1f}s: {e}") from e


def fallback_all_tests(
    parsed_files: list[ParsedTestFile],
    reason: str,
) -> LLMSelectionResponse:
    """Create a response that marks all tests as must_run."""
    selections = []
    for pf in parsed_files:
        for test in pf.tests:
            selections.append(
                TestSelection(
                    test_id=test.qualified_name,
                    granularity="test" if len(pf.tests) > 1 else "file",
                    classification=TestClassification.must_run,
                    reasoning=f"Fallback: {reason}",
                    confidence=0.0,
                )
            )
    return LLMSelectionResponse(
        summary=f"Fallback triggered: {reason}",
        selections=selections,
        fallback_recommended=True,
    )


def _call_with_structured_output(
    messages: list[dict[str, str]],
    config: LLMConfig,
    api_key: str,
    schema: dict[str, object],
) -> LLMSelectionResponse:
    """Tier 1: Use litellm's response_format for structured output."""
    max_retries = 1
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = litellm.completion(
                model=config.model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "TestSelectionResponse",
                        "schema": schema,
                        "strict": True,
                    },
                },
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                timeout=config.timeout_seconds,
                api_key=api_key,
            )

            content = response.choices[0].message.content
            if not content:
                raise LLMParseError("Empty response from LLM")

            return LLMSelectionResponse.model_validate_json(content)

        except litellm.Timeout as e:  # type: ignore[attr-defined]
            raise LLMTimeoutError(f"LLM timed out after {config.timeout_seconds}s") from e

        except (litellm.RateLimitError, litellm.InternalServerError) as e:  # type: ignore[attr-defined]
            last_error = e
            if attempt < max_retries:
                logger.warning("Retryable error (attempt %d): %s", attempt + 1, e)
                time.sleep(2)
                continue
            raise LLMError(f"LLM error after retries: {e}") from e

        except (litellm.AuthenticationError, litellm.BadRequestError) as e:  # type: ignore[attr-defined]
            raise LLMError(f"LLM error: {e}") from e

        except Exception as e:
            if "response_format" in str(e).lower() or "json_schema" in str(e).lower():
                raise LLMError(f"Structured output not supported: {e}") from e
            raise LLMError(f"LLM call failed: {e}") from e

    raise LLMError(f"LLM call failed after retries: {last_error}")


def _call_with_text_mode(
    messages: list[dict[str, str]],
    config: LLMConfig,
    api_key: str,
    schema: dict[str, object],
) -> LLMSelectionResponse:
    """Tier 2: Embed JSON schema in prompt and parse text response."""
    schema_instruction = (
        "\n\nYou MUST respond with ONLY valid JSON matching this schema:\n"
        f"```json\n{json.dumps(schema, indent=2)}\n```\n"
        "Do not include any text outside the JSON object."
    )

    # Append schema instruction to system message
    modified_messages = []
    for msg in messages:
        if msg["role"] == "system":
            modified_messages.append(
                {
                    "role": "system",
                    "content": msg["content"] + schema_instruction,
                }
            )
        else:
            modified_messages.append(msg)

    try:
        response = litellm.completion(
            model=config.model,
            messages=modified_messages,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout_seconds,
            api_key=api_key,
        )

        content = response.choices[0].message.content
        if not content:
            raise LLMParseError("Empty response from LLM")

        return _parse_json_from_text(content)

    except (LLMParseError, LLMTimeoutError):
        raise
    except litellm.Timeout as e:  # type: ignore[attr-defined]
        raise LLMTimeoutError(f"LLM timed out after {config.timeout_seconds}s") from e
    except Exception as e:
        raise LLMError(f"LLM text mode failed: {e}") from e


def _parse_json_from_text(text: str) -> LLMSelectionResponse:
    """Extract and parse JSON from LLM text response."""
    # Try direct parse first
    try:
        return LLMSelectionResponse.model_validate_json(text)
    except Exception:
        pass

    # Look for JSON block in markdown code fence
    json_str = None
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        json_str = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        json_str = text[start:end].strip()
    else:
        # Look for outermost { ... }
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            json_str = text[brace_start : brace_end + 1]

    if json_str:
        try:
            data = json.loads(json_str)
            return LLMSelectionResponse.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            raise LLMParseError(f"Found JSON but failed to parse: {e}") from e

    raise LLMParseError("Could not find valid JSON in LLM response")
