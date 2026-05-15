# Contributing

## Development Setup

- Python 3.11+
- Create and activate a virtual environment
- Install dependencies:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Running Tests

```bash
python -m pytest
```

## Safety Expectations

- Never commit secrets (private keys, API keys, passphrases).
- Do not add logs that include private keys, raw signatures, or unredacted secrets.
- Keep live trading gated behind explicit config flags and multiple runtime checks.
- Prefer deterministic, offline-safe tests; mock network and wallet operations.

## Code Quality

- Keep code type-hinted.
- Keep changes small and testable.
- Favor explicit error handling for external IO.

## Pull Requests

- Include a short description of what changed and why.
- Include test coverage for behavior changes.
- If changing security controls, describe the impact and threat model considerations.
