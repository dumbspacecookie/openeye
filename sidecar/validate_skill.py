"""
OpenEye Skill Validator
Validates community skill markdown files against the OpenEye skill schema.

Usage:
    python validate_skill.py path/to/skill.md
    python validate_skill.py path/to/skills/          # validate all .md files in directory
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


REQUIRED_FIELDS = ["name", "description", "domain", "procedure_id", "tags", "version", "author"]
VALID_DOMAINS = {"medical", "manufacturing", "field-service", "general"}
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
MAX_DESCRIPTION_LENGTH = 120


def _parse_yaml_front_matter(text: str) -> Tuple[Dict, str]:
    """Parse YAML-like front matter from markdown. Simple parser, no pyyaml dep."""
    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    yaml_text = text[3:end].strip()
    body = text[end + 3:].strip()
    result = {}

    for line in yaml_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Handle arrays: [item1, item2, item3]
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
            result[key] = [i for i in items if i]
        # Handle numbers
        elif re.match(r"^\d+(\.\d+)?$", value):
            result[key] = float(value) if "." in value else int(value)
        # Handle booleans
        elif value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
        else:
            result[key] = value.strip("'\"")

    return result, body


def validate_skill_file(path: str) -> Tuple[bool, List[str]]:
    """Validate a skill markdown file. Returns (is_valid, list_of_errors)."""
    errors = []

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return False, [f"Cannot read file: {e}"]

    front_matter, body = _parse_yaml_front_matter(content)

    if not front_matter:
        return False, ["No YAML front matter found (file must start with ---)"]

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in front_matter:
            errors.append(f"Missing required field: {field}")

    # Validate name pattern
    name = front_matter.get("name", "")
    if name and not NAME_PATTERN.match(name):
        errors.append(f"Name must be kebab-case (lowercase, hyphens only): got '{name}'")

    # Validate domain
    domain = front_matter.get("domain", "")
    if domain and domain not in VALID_DOMAINS:
        errors.append(f"Domain must be one of {sorted(VALID_DOMAINS)}: got '{domain}'")

    # Validate version (semver)
    version = str(front_matter.get("version", ""))
    if version and not VERSION_PATTERN.match(version):
        errors.append(f"Version must be semantic (e.g. 1.0.0): got '{version}'")

    # Validate description length
    desc = front_matter.get("description", "")
    if desc and len(desc) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"Description too long ({len(desc)} chars, max {MAX_DESCRIPTION_LENGTH})")

    # Validate tags is a list
    tags = front_matter.get("tags", [])
    if not isinstance(tags, list) or len(tags) == 0:
        errors.append("Tags must be a non-empty array")

    # Validate body has content
    if not body.strip():
        errors.append("Skill body is empty (must have content after front matter)")

    return len(errors) == 0, errors


def validate_directory(dir_path: str) -> Tuple[int, int, List[str]]:
    """Validate all .md files in a directory tree. Returns (passed, failed, errors)."""
    passed = 0
    failed = 0
    all_errors = []

    for root, _, files in os.walk(dir_path):
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, dir_path)
            valid, errors = validate_skill_file(path)
            if valid:
                print(f"  OK {rel}")
                passed += 1
            else:
                print(f"  FAIL {rel}")
                for e in errors:
                    print(f"    - {e}")
                failed += 1
                all_errors.extend(f"{rel}: {e}" for e in errors)

    return passed, failed, all_errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_skill.py <path>")
        print("  path can be a single .md file or a directory of skills")
        sys.exit(1)

    target = sys.argv[1]

    if os.path.isdir(target):
        print(f"Validating skills in {target}/\n")
        passed, failed, errors = validate_directory(target)
        print(f"\n{passed} passed, {failed} failed")
        sys.exit(1 if failed > 0 else 0)
    else:
        valid, errors = validate_skill_file(target)
        rel = os.path.basename(target)
        if valid:
            print(f"  OK {rel}")
            sys.exit(0)
        else:
            print(f"  FAIL {rel}")
            for e in errors:
                print(f"    - {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
