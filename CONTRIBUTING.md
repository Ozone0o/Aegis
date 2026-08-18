# Contributing to Aegis

Thanks for helping improve Aegis. Changes should preserve the explicit,
operator-controlled recovery model and include a deterministic test for every
new health or recovery behavior.

## Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check src tests examples
```

CI covers Python 3.10 through 3.13. ROS integration requires a sourced ROS 2
environment; the offline core and `aegis --help` must work without ROS. Never
add a recovery action that guesses how a particular robot should be restarted.

Update the README or `CHANGELOG.md` for user-facing configuration changes.
Do not commit caches, wheels, `__pycache__` directories, `.aegis` state, or ROS
build output. Pull requests must document safety implications and test both
success and failure paths for recovery actions.
