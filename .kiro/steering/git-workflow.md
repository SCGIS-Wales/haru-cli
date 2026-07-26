---
inclusion: always
---

# Git workflow

- `main` is protected. Never commit directly to `main`.
- Every change starts on a new branch off the latest `main`.
- Branch naming: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`, `test/<slug>`. Example: `feat/sso-pkce-login`.
- Commits follow Conventional Commits: `type(scope): summary`, for example `feat(auth): add PKCE authorization code login`.
- One logical change per commit; keep commits small and reviewable.
- Open a pull request into `main`; CI (ruff, mypy, pytest with coverage gate) must pass before merge.
- Rebase on `main` before merge; prefer a linear history. Delete the branch after merge.
- Each chunk from the specification is its own branch and pull request.