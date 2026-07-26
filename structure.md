---
inclusion: always
---

# Project structure

- `src/haru/` holds the package; tests live in `tests/unit` and `tests/integration`.
- Configuration lives in `config/*.yaml`; prompts in `config/prompts/`.
- One responsibility per module: auth, config, models, agents, tools, sessions, observability, commands.
- Commands in `commands/` are thin; business logic lives in the domain modules and is unit tested independently of Click.
- Naming: modules and functions snake_case; dataclasses PascalCase; constants UPPER_SNAKE_CASE.
- Imports ordered by ruff isort rules: standard library, third party, first party.
- Factory functions return Strands objects; they take typed config plus a boto3 session and never read global state.