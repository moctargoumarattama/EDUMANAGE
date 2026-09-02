from __future__ import annotations

import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


ROUTE_DIRS = [ROOT / "app" / "routes", ROOT / "app" / "admin", ROOT / "app" / "blueprints"]
TEMPLATE_DIRS = [ROOT / "app" / "templates", ROOT / "app" / "admin" / "templates"]
STATIC_JS_DIRS = [ROOT / "app" / "static" / "js"]


def literal(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return call_name(node.func)
    return None


def route_decorator_info(dec):
    if not isinstance(dec, ast.Call):
        return None
    name = call_name(dec.func)
    if not name or not name.endswith(".route"):
        return None
    url = literal(dec.args[0]) if dec.args else None
    methods = ["GET"]
    for kw in dec.keywords:
        if kw.arg == "methods":
            value = literal(kw.value)
            if value:
                methods = sorted(value)
    return {"blueprint_expr": name.rsplit(".", 1)[0], "url": url, "methods": methods}


def extract_string_calls(func, names):
    found = defaultdict(list)
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node.func)
        if name not in names:
            continue
        if node.args:
            val = literal(node.args[0])
            if isinstance(val, str):
                found[name].append({"value": val, "line": node.lineno})
    return found


def extract_model_and_form_usage(func, model_names, form_names):
    models = set()
    forms = set()
    ctor_kwargs = defaultdict(list)
    attr_uses = defaultdict(set)
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and node.id in model_names:
            models.add(node.id)
        if isinstance(node, ast.Call):
            name = call_name(node.func)
            if name in form_names or (name and name.endswith("Form")):
                forms.add(name)
            if name in model_names:
                models.add(name)
                for kw in node.keywords:
                    if kw.arg:
                        ctor_kwargs[name].append({"arg": kw.arg, "line": node.lineno})
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in model_names:
                attr_uses[node.value.id].add((node.attr, node.lineno))
    return sorted(models), sorted(forms), ctor_kwargs, attr_uses


def parse_routes(model_names, form_names):
    routes = []
    ctor_kwargs = defaultdict(list)
    attr_uses = defaultdict(set)
    for base in ROUTE_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.glob("*.py")):
            src = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src, filename=str(path))
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                route_infos = [route_decorator_info(d) for d in node.decorator_list]
                route_infos = [r for r in route_infos if r]
                if not route_infos:
                    continue
                decorators = [call_name(d.func) if isinstance(d, ast.Call) else call_name(d) for d in node.decorator_list]
                calls = extract_string_calls(node, {"render_template", "url_for", "redirect"})
                models, forms, kw, attrs = extract_model_and_form_usage(node, model_names, form_names)
                for model, items in kw.items():
                    ctor_kwargs[model].extend(items)
                for model, items in attrs.items():
                    attr_uses[model].update(items)
                for route_info in route_infos:
                    blueprint = "main"
                    if route_info["blueprint_expr"] in {"admin_bp", "admin"}:
                        blueprint = "admin"
                    elif route_info["blueprint_expr"] == "api_sync":
                        blueprint = "api_sync"
                    endpoint = f"{blueprint}.{node.name}"
                    routes.append({
                        "file": str(path.relative_to(ROOT)),
                        "line": node.lineno,
                        "function": node.name,
                        "endpoint_guess": endpoint,
                        "url": route_info["url"],
                        "methods": route_info["methods"],
                        "decorators": [d for d in decorators if d],
                        "templates": calls.get("render_template", []),
                        "url_for": calls.get("url_for", []),
                        "redirect_args": calls.get("redirect", []),
                        "models": models,
                        "forms": forms,
                    })
    return routes, ctor_kwargs, attr_uses


def endpoint_required_args(app):
    data = {}
    route_table = []
    for rule in app.url_map.iter_rules():
        methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
        data[rule.endpoint] = sorted(rule.arguments)
        route_table.append({
            "endpoint": rule.endpoint,
            "rule": str(rule),
            "methods": methods,
            "arguments": sorted(rule.arguments),
        })
    return data, sorted(route_table, key=lambda x: (x["endpoint"], x["rule"]))


URL_FOR_RE = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"](?P<args>[^)]*)\)")
FORM_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>", re.I | re.S)
ATTR_RE = re.compile(r"([a-zA-Z_:.-]+)\s*=\s*['\"]([^'\"]*)['\"]")
FETCH_RE = re.compile(r"(fetch|XMLHttpRequest|axios|window\.location|\$\.ajax)\s*\(?\s*['\"]([^'\"]+)['\"]?", re.I)
HARDCODED_URL_RE = re.compile(r"['\"](/[^'\"{}\s<>]+)['\"]")


def parse_template_refs(endpoint_args):
    rendered = set()
    template_refs = []
    forms = []
    js_refs = []
    all_templates = []
    for base in TEMPLATE_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.html")):
            rel = str(path.relative_to(base)).replace("\\", "/")
            all_templates.append({"base": str(base.relative_to(ROOT)), "template": rel, "file": str(path.relative_to(ROOT))})
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in URL_FOR_RE.finditer(text):
                endpoint = m.group(1)
                args = set(re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=", m.group("args")))
                required = set(endpoint_args.get(endpoint, []))
                template_refs.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": text[:m.start()].count("\n") + 1,
                    "endpoint": endpoint,
                    "args": sorted(args),
                    "exists": endpoint in endpoint_args,
                    "missing_args": sorted(required - args),
                    "extra_args": sorted(args - required),
                })
            for m in FORM_RE.finditer(text):
                attrs = dict(ATTR_RE.findall(m.group("attrs")))
                forms.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": text[:m.start()].count("\n") + 1,
                    "method": attrs.get("method", "GET").upper(),
                    "action": attrs.get("action", ""),
                })
            for m in FETCH_RE.finditer(text):
                js_refs.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": text[:m.start()].count("\n") + 1,
                    "kind": m.group(1),
                    "target": m.group(2),
                })
            for m in HARDCODED_URL_RE.finditer(text):
                target = m.group(1)
                if target.startswith(("/static", "/#")):
                    continue
                js_refs.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": text[:m.start()].count("\n") + 1,
                    "kind": "hardcoded-url",
                    "target": target,
                })
    return all_templates, template_refs, forms, js_refs


def parse_static_js_refs():
    refs = []
    for base in STATIC_JS_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.js")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in FETCH_RE.finditer(text):
                refs.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": text[:m.start()].count("\n") + 1,
                    "kind": m.group(1),
                    "target": m.group(2),
                })
            for m in HARDCODED_URL_RE.finditer(text):
                refs.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": text[:m.start()].count("\n") + 1,
                    "kind": "hardcoded-url",
                    "target": m.group(1),
                })
    return refs


def smoke_tests(app, db, models):
    results = []
    original_log_correction = getattr(app, "log_correction", None)
    app.log_correction = lambda *args, **kwargs: None
    safe_endpoints = [
        "main.index", "main.login", "main.aide", "main.dashboard", "main.admin_dashboard",
        "main.eleves", "main.professeurs", "main.cours", "main.notes", "main.absences",
        "main.paiements", "main.bulletins", "main.rapports", "main.alertes",
        "main.liste_classes", "main.gestion_annees", "main.admin_emplois",
        "main.parent_dashboard", "main.portal_parent", "main.enseignant_dashboard",
        "main.enseignant_home", "main.notifications", "main.recherche",
        "main.voir_inscriptions", "main.imports_historique", "main.profile",
    ]
    rules = {r.endpoint: r for r in app.url_map.iter_rules()}
    users_by_role = {}
    with app.app_context():
        Utilisateur = getattr(models, "Utilisateur", None)
        if Utilisateur:
            for role in ["super_admin", "admin", "enseignant", "professeur", "parent", "eleve"]:
                user = Utilisateur.query.filter_by(role=role).first()
                if user:
                    users_by_role[role] = user.id
    def set_user(client, user_id):
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
            sess["_id"] = "audit"
    for role, user_id in users_by_role.items():
        with app.test_client() as client:
            set_user(client, user_id)
            for endpoint in safe_endpoints:
                rule = rules.get(endpoint)
                if not rule or rule.arguments or "GET" not in rule.methods:
                    continue
                try:
                    resp = client.get(str(rule), follow_redirects=False)
                    status = resp.status_code
                    results.append({"role": role, "endpoint": endpoint, "url": str(rule), "status": status, "location": resp.headers.get("Location")})
                except Exception as exc:
                    results.append({"role": role, "endpoint": endpoint, "url": str(rule), "error": type(exc).__name__, "message": str(exc)[:300]})
                finally:
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
    if original_log_correction is not None:
        app.log_correction = original_log_correction
    return {"users_by_role": users_by_role, "results": results}


def scan_placeholders_and_exceptions():
    patterns = re.compile(r"\bexcept\s*:|\bexcept Exception\b|\bpass\b|TODO|FIXME|NotImplemented|return\s+\{\}|return\s+\[\]|TON_|placeholder|fake|demo|test", re.I)
    hits = []
    for path in (ROOT / "app").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".html", ".js"} and "__pycache__" not in path.parts:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                if patterns.search(line):
                    hits.append({"file": str(path.relative_to(ROOT)), "line": i, "text": line.strip()[:220]})
    return hits


def main():
    import logging
    logging.disable(logging.CRITICAL)
    from app import create_app, db
    from app import models
    from app.forms import __dict__ as forms_dict

    app = create_app()
    model_names = {name for name, value in models.__dict__.items() if isinstance(value, type) and hasattr(value, "__tablename__")}
    form_names = {name for name, value in forms_dict.items() if isinstance(value, type) and name.endswith("Form")}
    with app.app_context():
        model_attrs = {}
        for name in sorted(model_names):
            cls = getattr(models, name)
            try:
                model_attrs[name] = sorted(set(cls.__mapper__.attrs.keys()) | set(c.name for c in cls.__table__.columns))
            except Exception:
                model_attrs[name] = []
    endpoint_args, route_table = endpoint_required_args(app)
    routes, ctor_kwargs, attr_uses = parse_routes(model_names, form_names)
    all_templates, template_refs, forms, template_js_refs = parse_template_refs(endpoint_args)
    static_js_refs = parse_static_js_refs()

    rendered_templates = {item["value"] for route in routes for item in route["templates"]}
    orphan_templates = []
    for item in all_templates:
        name = item["template"]
        if name in {"base.html", "_pagination.html"} or name.startswith("modals/"):
            continue
        if name not in rendered_templates:
            orphan_templates.append(item)

    invalid_template_refs = [r for r in template_refs if not r["exists"] or r["missing_args"]]
    suspicious_extra_args = [r for r in template_refs if r["exists"] and r["extra_args"]]

    model_issues = []
    for model, items in ctor_kwargs.items():
        attrs = set(model_attrs.get(model, []))
        for item in items:
            if item["arg"] not in attrs:
                model_issues.append({"type": "ctor_kwarg", "model": model, **item})
    for model, items in attr_uses.items():
        attrs = set(model_attrs.get(model, []))
        for attr, line in sorted(items):
            if attr not in attrs and attr not in {"query"}:
                model_issues.append({"type": "class_attr", "model": model, "arg": attr, "line": line})

    js_refs = template_js_refs + static_js_refs
    smoke = smoke_tests(app, db, models)
    placeholders = scan_placeholders_and_exceptions()

    report = {
        "route_count": len(route_table),
        "endpoint_count": len(endpoint_args),
        "routes": routes,
        "route_table": route_table,
        "templates": {
            "all_count": len(all_templates),
            "rendered": sorted(rendered_templates),
            "orphan_candidates": orphan_templates,
            "invalid_url_for": invalid_template_refs,
            "suspicious_extra_args": suspicious_extra_args,
            "forms": forms,
        },
        "javascript": js_refs,
        "models": {
            "issues": model_issues,
        },
        "smoke": smoke,
        "placeholders_exceptions": placeholders,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
