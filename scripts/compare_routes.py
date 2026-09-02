from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "backups" / "routes_before_refactor.json"
AFTER = ROOT / "backups" / "routes_after_refactor.json"
sys.path.insert(0, str(ROOT))


def normalize(rows):
    return {
        (row["rule"], row["endpoint"], tuple(row["methods"]))
        for row in rows
    }


def current_routes():
    logging.disable(logging.CRITICAL)
    from app import create_app

    app = create_app()
    rows = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.endpoint, r.rule)):
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        rows.append({"rule": rule.rule, "endpoint": rule.endpoint, "methods": methods})
    return rows


def scan_main_url_for_refs(endpoints):
    pattern = re.compile(r"url_for\(\s*['\"]main\.([A-Za-z0-9_]+)['\"]")
    refs = {}
    for base in (ROOT / "app",):
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".html", ".js"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                refs.setdefault(match.group(1), []).append((str(path.relative_to(ROOT)), line))

    return {name: refs[name] for name in sorted(refs) if f"main.{name}" not in endpoints}


def main():
    before = json.loads(BEFORE.read_text(encoding="utf-8"))
    after = current_routes()
    AFTER.write_text(json.dumps(after, indent=2, ensure_ascii=False), encoding="utf-8")

    before_set = normalize(before)
    after_set = normalize(after)
    endpoints = {row["endpoint"] for row in after}

    print(f"BEFORE_ROUTE_COUNT {len(before)}")
    print(f"AFTER_ROUTE_COUNT {len(after)}")
    print(f"BEFORE_ENDPOINT_COUNT {len({row['endpoint'] for row in before})}")
    print(f"AFTER_ENDPOINT_COUNT {len(endpoints)}")
    print(f"MISSING_ROUTES {len(before_set - after_set)}")
    for item in sorted(before_set - after_set):
        print(f"MISSING {item}")
    print(f"ADDED_ROUTES {len(after_set - before_set)}")
    for item in sorted(after_set - before_set):
        print(f"ADDED {item}")

    missing_refs = scan_main_url_for_refs(endpoints)
    print(f"MISSING_MAIN_ENDPOINT_REFS {len(missing_refs)}")
    for endpoint, locations in missing_refs.items():
        preview = ", ".join(f"{path}:{line}" for path, line in locations[:6])
        print(f"REF main.{endpoint} -> {preview}")


if __name__ == "__main__":
    main()
