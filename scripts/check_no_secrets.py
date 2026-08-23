"""Fail CI when tracked files contain credential-shaped values.

This is intentionally small and dependency-free. It complements repository hosting
secret scanning by enforcing the project's local rule: real `.env*` files and private
keys are never tracked, and common provider-token formats never enter the source tree.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ENV_FILES = {".env.example"}

TOKEN_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "secret key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}

ASSIGNMENT = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?"
    r"([A-Z][A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|CLIENT_SECRET|PRIVATE_KEY))"
    r"[ \t]*=[ \t]*([^\s#]*)"
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / name.decode() for name in result.stdout.split(b"\0") if name]


def is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    return (
        not normalized
        or normalized.startswith(("<", "${", "your_", "test-", "test_"))
        or normalized in {"placeholder", "unset", "none"}
    )


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT)
        if path.name.startswith(".env") and path.name not in ALLOWED_ENV_FILES:
            findings.append(f"tracked environment file: {relative}")
            continue

        data = path.read_bytes()
        if b"\0" in data:
            continue

        for label, pattern in TOKEN_PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{label} pattern: {relative}")

        text = data.decode("utf-8", errors="ignore")
        for match in ASSIGNMENT.finditer(text):
            if not is_placeholder(match.group(2)):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"credential value assigned: {relative}:{line} ({match.group(1)})")

    if findings:
        print("Secret gate failed:")
        for finding in sorted(set(findings)):
            print(f"- {finding}")
        return 1

    print("Secret gate passed: no tracked environment files or credential-shaped values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
