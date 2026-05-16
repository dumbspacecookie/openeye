"""
Install one or more community skills into a running OpenEye sidecar.

Reads community-skills/index.json, finds the requested skills, and POSTs
them to /skills/write so they show up in the agent's recall set.

Usage:
    python community-skills/install.py bolt-assembly-m6
    python community-skills/install.py --all
    python community-skills/install.py --domain manufacturing
    python community-skills/install.py --list
"""

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [install] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_index(root: Path) -> dict:
    with open(root / "index.json", "r", encoding="utf-8") as f:
        return json.load(f)


def read_skill(root: Path, rel_path: str) -> tuple[str, str]:
    """Returns (content_without_frontmatter, parsed_description)."""
    path = root / rel_path
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    description = ""
    body = text
    # Strip YAML frontmatter if present
    if text.startswith("---"):
        m = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if m:
            frontmatter, body = m.group(1), m.group(2).lstrip("\n")
            for line in frontmatter.splitlines():
                if line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
                    break
    return body, description


def post_skill(base_url: str, token: str, payload: dict) -> bool:
    url = f"{base_url.rstrip('/')}/skills/write"
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        logger.error("HTTP %d installing skill: %s", e.code, e.read()[:200])
        return False
    except urllib.error.URLError as e:
        logger.error("Connection failed: %s", e)
        return False


def install(root: Path, entries: list, base_url: str, token: str) -> int:
    installed = 0
    for entry in entries:
        content, fallback_desc = read_skill(root, entry["path"])
        description = entry.get("description") or fallback_desc
        payload = {
            "name": entry["name"],
            "content": content,
            "description": description,
            "domain": entry.get("domain", "general"),
            "source": "community",
        }
        if entry.get("advisory"):
            payload["content"] = (
                f"<!-- ADVISORY: {entry['advisory']} -->\n\n" + payload["content"]
            )
        ok = post_skill(base_url, token, payload)
        if ok:
            logger.info("Installed: %s (%s)", entry["name"], entry["domain"])
            installed += 1
        else:
            logger.warning("Failed: %s", entry["name"])
    return installed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*", help="Skill names to install")
    parser.add_argument("--all", action="store_true", help="Install every skill in the index")
    parser.add_argument("--domain", help="Install all skills in this domain")
    parser.add_argument("--list", action="store_true", help="Print the index and exit")
    parser.add_argument("--url", default=os.getenv("OPENEYE_URL", "http://127.0.0.1:7770"),
                        help="Sidecar base URL")
    parser.add_argument("--token", default=os.getenv("OPENEYE_SIDECAR_TOKEN", ""),
                        help="Sidecar bearer token (only if you set OPENEYE_SIDECAR_TOKEN)")
    args = parser.parse_args()

    root = Path(__file__).parent
    index = load_index(root)
    skills = index["skills"]

    if args.list:
        print(f"{'NAME':<30} {'DOMAIN':<16} {'TAGS':<32} DESCRIPTION")
        print("-" * 100)
        for s in skills:
            print(f"{s['name']:<30} {s['domain']:<16} {','.join(s.get('tags', [])):<32} {s['description'][:50]}")
        return 0

    if args.all:
        selected = skills
    elif args.domain:
        selected = [s for s in skills if s["domain"] == args.domain]
        if not selected:
            logger.error("No skills found in domain '%s'", args.domain)
            return 1
    else:
        if not args.names:
            parser.error("Provide skill names, --all, --domain, or --list")
        by_name = {s["name"]: s for s in skills}
        selected = []
        for n in args.names:
            if n not in by_name:
                logger.error("Unknown skill '%s'. Run --list to see options.", n)
                return 1
            selected.append(by_name[n])

    count = install(root, selected, args.url, args.token)
    logger.info("Installed %d of %d skills", count, len(selected))
    return 0 if count == len(selected) else 2


if __name__ == "__main__":
    sys.exit(main())
