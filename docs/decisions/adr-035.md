# ADR-035: Python Target 3.12 and a Single Ruff Version Across Pre-Commit and Venv

## Status

Accepted.

## Context

Tooling debt accumulated as a side note across several rounds and reached the point where the two linters had lost their value as fitness functions.

Three facts were in tension. The project runs on Python 3.13 but declared `requires-python = ">=3.11"`, an unconsidered floor rather than a real deployment target. The three LLM clients use PEP-695 generics (`def parse[T: BaseModel]`), which are valid from 3.12 but invalid syntax against 3.11. Both linters enforced 3.11: mypy (`python_version = "3.11"`) aborted at the first client it parsed, so it never checked the rest of `src`, and ruff reported the generics as invalid-syntax errors.

Separately, the pre-commit ruff hook was pinned at `v0.4.4` and the dev extra at `ruff==0.4.4`, while the venv carried `ruff 0.15.13`. The two formatted differently (notably implicit string-concatenation handling), so a commit reformatted by the venv was re-reformatted by the hook and vice versa, a recurring retry cost. The 0.4.4-era ruff config also still ignored `ANN101`/`ANN102`, rules that ruff 0.15 removed, leaving inert config.

## Decision

Move forward on both axes rather than pinning the toolchain to the old floor.

- `requires-python = ">=3.12"`. The PEP-695 generics become legal with no code change.
- mypy `python_version = "3.12"`, so it stops aborting on the generics and checks the whole tree.
- Converge ruff up, not down: `ruff==0.15.13` in the dev extra (the version the venv already had) and the `ruff-pre-commit` hook rev at `v0.15.13`, so pre-commit and the venv run one ruff version.
- Remove the dead `ANN101`/`ANN102` lint ignores.
- Add `[tool.hatch.metadata] allow-direct-references = true`. The `de_core_news_md` spaCy model is a direct-URL dependency; modern hatchling rejects direct references without this, so `pip install -e .[dev]` failed at metadata generation until it was set.

## Rationale

The codebase already runs 3.13; 3.11 was a floor nobody chose. Raising the declared floor to 3.12 makes the syntax the code already uses legal under the tools that check it, with no code change, which is the cheapest way to resolve the abort.

Converging ruff up keeps the modern formatter and rule set the venv already ran, and closes the formatting-retry conflict by giving pre-commit and the venv one authority. Converging down would have meant reinstalling an old ruff everywhere and re-suppressing modern rules, debt in the other direction.

Pinning ruff exact (rather than a floor) is the same posture the dev extra takes for the other lint and structural tools (ADR-034, import-linter pinned exact): the formatter is a fitness function, and a fitness function whose version floats produces non-reproducible reformats.

CLAUDE.md carries the working-guidance note locally, but it is gitignored in this repo, so this ADR is the durable, version-tracked record of the target and version decision.

## Consequences

Positive:

- `ruff check .` and `ruff format --check .` are clean across the whole tree, and `mypy src` checks all 55 files instead of aborting at the first client. Both regain their value as fitness functions on one agreed version.
- Pre-commit ruff and venv ruff report the same version (0.15.13); the formatting-retry conflict is gone.
- The PEP-695 generics are kept, not rewritten to the old `TypeVar` form, so the clients stay on the modern idiom.

Negative:

- The supported-Python floor rose from 3.11 to 3.12. The project never targeted 3.11 for deployment, so this codifies reality rather than dropping support anyone relied on, but a contributor on 3.11 can no longer install it.
- One more hatch config block (`allow-direct-references`), the cost of keeping the spaCy model as a pinned direct URL.

## Alternatives Considered

A. Keep 3.11 and rewrite the PEP-695 generics back to module-level `TypeVar`. Rejected: it changes working code to satisfy a floor nobody chose, and moves the clients off the modern idiom the rest of the round leans toward.

B. Converge ruff down to 0.4.4 in the venv. Rejected: it reinstalls an old formatter everywhere, re-introduces the removed-rule ignores, and abandons the 0.15 fixes the venv already had. Converging up costs nothing the venv was not already paying.

C. Leave the pre-commit hook and the dev pin at different versions and accept the retry cost. Rejected: this is the exact discipline-not-mechanism state the round closes; one version across both is the only way the conflict cannot recur.

## References

- ADR-034: tooling config in `pyproject.toml` (`[tool.importlinter]`, dev tools pinned exact); the convention this follows for the ruff pin.
- ADR-025: PII-masking scope and the `de_core_news_md` spaCy model the direct-URL dependency carries.
- Round 20 Hygiene B (this round): the version bump, the StrEnum/UP047 conversions the 3.12 target and 0.15 bump surfaced, and the first full mypy sweep of `src`.
- `pyproject.toml` (`requires-python`, `[tool.ruff]`, `[tool.mypy]`, `[tool.hatch.metadata]`) and `.pre-commit-config.yaml`: the pinned versions and target.
