# CyBot Test Suite

Complete pytest test suite for CyBot. All tests run **fully offline** — no Discord connection, no Gemini API calls, no GCS bucket needed.

---

## Quick Start

### 1. Install test dependencies

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio
```

### 2. Run all tests

```bash
pytest tests/ -v
```

### 3. Run a single module

```bash
pytest tests/test_prompt_builder.py -v
pytest tests/test_persona.py -v
pytest tests/test_config.py -v
pytest tests/test_gemini_client.py -v
pytest tests/test_webhook_manager.py -v
pytest tests/test_main_utils.py -v
pytest tests/test_integration_generate.py -v
```

### 4. Run with coverage (optional)

```bash
pip install pytest-cov
pytest tests/ --cov=. --cov-report=term-missing -v
```

---

## Test Modules

| File                           | Coverage                                                                                |
| ------------------------------ | --------------------------------------------------------------------------------------- |
| `test_prompt_builder.py`       | `ai/prompt_builder.py` — message building, exclusion/slang injection                    |
| `test_persona.py`              | `ai/persona.py` — system prompt rendering, overrides, serialisation                     |
| `test_config.py`               | `bot/config.py` — env validation, JSON loading, type coercion, channel/admin management |
| `test_gemini_client.py`        | `ai/client.py` — role conversion, error wrapping, sanitised messages                    |
| `test_webhook_manager.py`      | `bot/webhook_manager.py` — cache, create, race condition fallback, send, cleanup        |
| `test_main_utils.py`           | `bot/main.py` — URL extraction/stripping/surfacing, exclusion violation detection       |
| `test_integration_generate.py` | End-to-end `generate_post()` / `generate_interaction()` pipeline                        |

---

## Local vs Cloud Run

Both environments work identically — all external I/O (Discord, Gemini API, GCS) is
mocked. No `.env` file is required to run tests; fixtures set the necessary env vars
via `monkeypatch`.

**On Cloud Run**, you can run the tests inside the container during a build step:

```dockerfile
# Example Dockerfile test stage (optional)
RUN pip install pytest pytest-asyncio && pytest tests/ -v
```

Or as a Cloud Build step before deploy:

```yaml
# cloudbuild.yaml (optional)
steps:
  - name: "python:3.12-slim"
    entrypoint: bash
    args:
      - "-c"
      - "pip install -r requirements.txt pytest pytest-asyncio && pytest tests/ -v"
```

---

## Shared Fixtures (`conftest.py`)

| Fixture              | Description                                                             |
| -------------------- | ----------------------------------------------------------------------- |
| `minimal_persona`    | In-memory `Persona` with predictable test data. No disk I/O.            |
| `minimal_config_env` | Patches `os.environ` with the minimum required env vars for `Config()`. |

---

## Adding New Tests

1. Create `tests/test_<module>.py`
2. Use the shared fixtures from `conftest.py` where applicable
3. For async tests, decorate with `@pytest.mark.asyncio`
4. Mock all external I/O — never make real network calls from tests

---

## Async Tests

Async tests use `pytest-asyncio`. The `asyncio_mode` is set per-test via `@pytest.mark.asyncio`.
If you see `RuntimeWarning: coroutine was never awaited`, ensure the decorator is present.
