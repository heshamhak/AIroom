from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProjectTemplate:
    id: str
    name: str
    title_hint: str
    prompt: str
    verify_command: str


BUILTIN_TEMPLATES: list[ProjectTemplate] = [
    ProjectTemplate(
        id="python_cli",
        name="أداة Python CLI + اختبارات",
        title_hint="أداة سطر أوامر بايثون",
        prompt="Build a modular Python CLI tool with clean error handling and pytest unit tests.",
        verify_command="pytest -q",
    ),
    ProjectTemplate(
        id="fastapi_sqlite",
        name="خدمة FastAPI + SQLite",
        title_hint="خدمة ويب FastAPI",
        prompt="Design a FastAPI service backed by SQLite WAL with Pydantic schemas, token auth, and pytest integration tests.",
        verify_command="pytest -q",
    ),
    ProjectTemplate(
        id="flutter_app",
        name="تطبيق Flutter متعدد المنصات",
        title_hint="تطبيق Flutter",
        prompt="Design a clean cross-platform Flutter UI with Material 3, state management, and widget tests.",
        verify_command="flutter test",
    ),
    ProjectTemplate(
        id="security_audit",
        name="تدقيق أمني وجودة الكود",
        title_hint="تدقيق أمني للمشروع",
        prompt="Audit the current workspace RepoMap for security, path-traversal, secret leaks, and missing tests, then propose concrete fixes.",
        verify_command="./scripts/validate_release.sh",
    ),
]


def list_templates() -> list[dict]:
    return [asdict(t) for t in BUILTIN_TEMPLATES]
