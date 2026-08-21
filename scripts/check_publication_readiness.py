#!/usr/bin/env python3
"""Check repository publication hygiene without making policy decisions."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
STANDARD_FILES = {
    "README.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "LICENSE",
    "PRIVACY.md",
    "SECURITY.md",
    "SUPPORT.md",
    "THIRD_PARTY_NOTICES.md",
    "docs/PUBLIC_RELEASE_CHECKLIST.md",
    ".github/ISSUE_TEMPLATE/bug.yml",
    ".github/ISSUE_TEMPLATE/feature.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/pull_request_template.md",
}
FORBIDDEN_TRACKED_PARTS = {
    ".atlas",
    ".agent-token-manager",
    ".evaluation-data",
    ".evaluation-tools",
    "node_modules",
    "__pycache__",
}
SENSITIVE_TEXT = {
    "absolute macOS user path": re.compile(r"/U" + r"sers/[^/\s]+/"),
    "private key material": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
}
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def tracked_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return [line for line in completed.stdout.splitlines() if line]


def check_links(files: list[str], errors: list[str]) -> None:
    for relative in files:
        if not relative.endswith(".md"):
            continue
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split()[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            local = unquote(target.split("#", 1)[0])
            if local and not (path.parent / local).resolve().exists():
                errors.append(f"broken local link: {relative} -> {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("pre-public", "public"), default="pre-public")
    args = parser.parse_args()
    errors: list[str] = []
    tracked = tracked_files()
    tracked_set = set(tracked)

    missing = sorted(item for item in STANDARD_FILES if not (ROOT / item).is_file())
    errors.extend(f"missing standard file: {item}" for item in missing)

    audit_files = sorted(tracked_set | STANDARD_FILES)
    for relative in audit_files:
        if relative in tracked_set and any(part in FORBIDDEN_TRACKED_PARTS for part in Path(relative).parts):
            errors.append(f"forbidden generated/local path is tracked: {relative}")
        path = ROOT / relative
        if path.is_file() and path.stat().st_size <= 2_000_000:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for label, pattern in SENSITIVE_TEXT.items():
                if pattern.search(text):
                    errors.append(f"{label} in tracked file: {relative}")

    check_links(sorted(tracked_set | STANDARD_FILES), errors)

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    expected_urls = {"Repository", "Documentation", "Changelog", "Issues"}
    missing_urls = sorted(expected_urls - set(project.get("urls", {})))
    errors.extend(f"missing project URL: {item}" for item in missing_urls)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    license_candidates = sorted(
        path.name for path in ROOT.iterdir() if path.is_file() and path.name.upper().startswith("LICENSE")
    )
    if args.mode == "pre-public":
        if license_candidates:
            errors.append("pre-public mode found a product license; switch the gate to --mode public")
        if "product license and public repository decision remain pending" not in readme:
            errors.append("README does not state the unresolved pre-public license boundary")
    else:
        if not license_candidates:
            errors.append("public mode requires an explicit product LICENSE file")
        if "remain pending" in readme:
            errors.append("public mode still contains a pending-decision statement")
        if project.get("license") != "Apache-2.0":
            errors.append("public mode requires the selected Apache-2.0 package license")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"publication readiness: PASS ({args.mode}; {len(tracked)} tracked files; "
        f"{len(STANDARD_FILES)} standard files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
