from pathlib import Path

from flask import flash, render_template

from app import create_app


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-xss"
    WTF_CSRF_ENABLED = False


def test_flash_messages_escape_html_payloads():
    app = create_app(TestConfig)

    with app.test_request_context("/"):
        flash('<img src=x onerror=alert(1)>', "success")
        html = render_template("partials/_flash_messages.html")

    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert '<img src=x onerror=alert(1)>' not in html
    assert "message|safe" not in Path("app/templates/partials/_flash_messages.html").read_text(encoding="utf-8")


def test_offline_notifications_do_not_inject_dynamic_html():
    js = Path("app/static/js/offline-forms.js").read_text(encoding="utf-8")

    assert "alertEl.innerHTML" not in js
    assert "<strong>${studentName}" not in js
    assert "<strong>${payload.prenom}" not in js
    assert "node.textContent = String(line || '')" in js
