# Contributing to AIroom

Thank you for contributing to **AIroom**!

## Development Setup

1. **Backend**:
   ```bash
   cd backend
   python3 -m venv .venv
   .venv/bin/pip install -r requirements-dev.txt
   .venv/bin/pytest -v tests/
   ```
2. **Frontend (Flutter)**:
   ```bash
   cd app
   flutter pub get
   flutter analyze
   flutter test
   ```
3. **Full Validation Gate**:
   ```bash
   ./scripts/validate_release.sh
   ```

## Rules for Issues & Pull Requests

- **Never** include real `api_token` values, `.db` files, `.env` files, or Chrome profile/cookie directories in commits, issues, PRs, or logs.
- All backend changes must include hermetic unit/integration tests using `tmp_path` (no live Chrome or external accounts required in CI).
- Keep `flutter analyze` free of warnings and errors.
