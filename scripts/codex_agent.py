import json
import os
from pathlib import Path
from openai import OpenAI

ROOT = Path(".").resolve()

EXCLUDED_DIRS = {
    ".git", ".github", "__pycache__", ".venv", "venv", "env",
    "node_modules", "dist", "build", ".next", ".idea", ".vscode"
}

EXCLUDED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".bin"
}

MAX_FILES = 100
MAX_CHARS_PER_FILE = 12000


def should_skip(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return True
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    return False


def read_project_files():
    collected = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if should_skip(path):
            continue

        rel = path.relative_to(ROOT).as_posix()

        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue

        if len(content) > MAX_CHARS_PER_FILE:
            content = content[:MAX_CHARS_PER_FILE] + "\n...[truncated]..."

        collected.append({
            "path": rel,
            "content": content
        })

        if len(collected) >= MAX_FILES:
            break

    return collected


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    task = os.environ.get("TASK", "Improve the project")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")

    client = OpenAI(api_key=api_key)
    files = read_project_files()

    system_prompt = """
Ты автономный AI-разработчик для GitHub-репозитория.

Твоя задача:
1. Изучить переданные файлы проекта.
2. Выполнить задачу пользователя.
3. Вернуть СТРОГО JSON без markdown, без пояснений, без лишнего текста.

Формат ответа:
{
  "summary": "кратко что изменено",
  "files": [
    {
      "path": "relative/path/to/file.ext",
      "content": "полный новый текст файла"
    }
  ]
}

Правила:
- Возвращай только новые или изменённые файлы.
- Для каждого файла возвращай полный итоговый текст.
- Не возвращай бинарные файлы.
- Делай минимально необходимые изменения.
- Если ничего менять не нужно, верни:
  {"summary":"no changes","files":[]}
"""

    user_payload = {
        "task": task,
        "project_files": files,
    }

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}
        ],
    )

    raw = response.output_text.strip()
    data = json.loads(raw)

    changed = 0
    for item in data.get("files", []):
        rel_path = item["path"]
        content = item["content"]

        target = ROOT / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        changed += 1

    print(data.get("summary", "No summary"))
    print(f"Changed files: {changed}")


if __name__ == "__main__":
    main()
