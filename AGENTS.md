# Repository Guidelines

## Project Structure

Analyze chat histories from ChatGPT, Claude, Gemini to identify unfinished projects and prioritize them.

- Key files: `setup.py`.

## Development and Validation

Use Python >=3.8. Run commands from the repository root unless the component documentation says otherwise. Configure local dependencies and test services before application tests.

- `python3 -m venv .venv` — create an isolated Python environment.
- `.venv/bin/python -m pip install -e '.[dev]'` — install the local Python package and development extras.
- `.venv/bin/python -m pytest` — run Python tests after installing the documented test dependencies.

## Coding and Testing

Match the indentation and naming of neighboring files; avoid unrelated reformatting. Tests use pytest. Keep changes focused and follow existing test filenames. Add regression coverage for behavior changes, using isolated fixtures instead of live customer data. For documentation-only edits, verify commands, local links, and `git diff --check`.

## Working Agreement

Read `CONTRIBUTING.md` and `SECURITY.md` before contributing. Honor directory-specific agent instructions. Preserve existing local changes and use a separate branch or worktree when other work is in progress. Keep credentials, private datasets, and generated artifacts out of commits. Deployment, publishing, and live service changes require authorization for that environment.

Use concise commit subjects consistent with recent history (for example, `docs: clarify setup`). Pull requests should explain the change, link relevant issues, and record validation results and any skipped checks. Include screenshots when user-visible behavior changes.

## Licensing

MIT licensed; see [LICENSE](LICENSE). Preserve third-party notices.
