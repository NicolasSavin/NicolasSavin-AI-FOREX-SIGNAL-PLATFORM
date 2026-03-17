import json
import os
from pathlib import Path
from openai import OpenAI

ROOT = Path(".").resolve()

EXCLUDED_DIRS = {
    ".git", ".github", "__pycache__", ".venv", "venv", "env",
    "node_modules", "dist", "build", ".next", ".idea", ".vscode",
    "coverage", ".pytest_cache", ".mypy_cache"
}

EXCLUDED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".bin",
    ".csv", ".parquet", ".feather"
}

IMPORTANT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml"
}

PRIORITY_FILES = {
    "README.md",
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    ".gitignore",
}

MAX_FILES = 25
MAX_CHARS_PER_FILE = 4000


def should_skip(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return True
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    return False


def score_file(path: Path) -> int:
    score = 0
    name = path.name.lower()
    rel = path.as_posix().lower()

    if path.name in PRIORITY_FILES:
        score += 100

    if path.suffix.lower() in IMPORTANT_SUFFIXES:
        score += 20

    if "strategy" in rel or "signal" in rel or "trade" in rel:
        score += 30

    if "test" in rel:
        score += 10

    if rel.startswith("scripts/"):
        score += 5

    return score


def collect_files():
    candidates = []

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if should_skip(path):
            continue
        if path.suffix.lower() not in IMPORTANT_SUFFIXES and path.name not in PRIORITY_FILES:
            continue
        candidates.append(path)

    candidates.sort(key=score_file, reverse=True)

    result = []
    for path in candidates[:MAX_FILES]:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue

        if len(content) > MAX_CHARS_PER_FILE:
            content = content[:MAX_CHARS_PER_FILE] + "\n...[truncated]..."

        result.append({
            "path": path.relative_to(ROOT).as_posix(),
            "content": content
        })

    return result


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    task = os.environ.get("TASK", "Improve the project")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")

    client = OpenAI(api_key=api_key)
    files = collect_files()

    system_prompt = """
Ты автономный AI-разработчик для GitHub-репозитория.

Верни строго JSON без markdown:

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
- Меняй минимально необходимое.
- Возвращай только новые или изменённые файлы.
- Если информации недостаточно, меняй только очевидные файлы.
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
