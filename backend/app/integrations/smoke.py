"""Живая проверка подключений из командной строки: python -m app.integrations.smoke [коды…](integration-spec §16.4).

Для каждого подключения: ключей нет - "{code}: не настроен - нет {имена}"(без сети); иначе - connector.check(…) в
эфемерном контексте(без БД: публикации не сохраняются, квота не учитывается) → "{code}: {статус} - {пояснение}" и
несекретные сведения. Значения ключей не печатаются(вывод проходит маскирование). Код выхода - 0(это отчёт, не тест);
2 - неизвестный код подключения.
"""
import argparse
import json
import sys

from .. import logs
from ..ingest import scrub
from ..settings import get_settings
from . import base, registry


def _line(text: str) -> None:
    print(logs.redact(text, limit=None))


def check_one(code: str) -> str:
    """Проверка одного подключения → строка отчёта(маскированная)."""
    from .context import RunContext

    conn = registry.get(code)
    s = get_settings()
    if isinstance(conn, registry.BrokenConnector):
        return f"{code}: ошибка — {registry.BROKEN}"
    missing = conn.missing(s)
    if missing:
        env = [n for n in missing if n in tuple(conn.required_env or ())]
        if env:
            return f"{code}: не настроен — нет {', '.join(env)}"
        # переменные заданы, не хватает условия(сессия Telegram) - пояснение самого подключения
        return f"{code}: не настроен — {conn.missing_detail(s, None) or ', '.join(missing)}"
    computed = None
    if not (conn.capabilities or {}).get("test"):
        computed = conn.computed_status(None, None, None) if conn.kind == "analyzer" else None
        if computed is not None:
            return f"{code}: {conn.status_label(computed[0])} — {computed[1]}"
        label = conn.status_label("waiting") if conn.kind == "intake" else "настроен"
        return f"{code}: {label} — проверки нет (ключи на сервере заданы)" + (
            "; контракт адаптера не сверен" if conn.contract_verified is False else "")
    ctx = RunContext.ephemeral(code)
    try:
        res = conn.check(ctx)
    except base.RateLimited as e:
        return f"{code}: {conn.status_label('rate_limited')} — {e.detail}"
    except base.ConnectorError as e:
        status = {"not_configured": "not_configured", "auth_expired": "expired", "access_denied": "denied",
                  "access_check": "access_check"}.get(e.code, "error")
        return f"{code}: {conn.status_label(status)} — {e.detail}"
    except Exception as e:
        return f"{code}: ошибка — {type(e).__name__}: {logs.redact(str(e))}"
    status = "ready" if res.ok else "error"
    out = f"{code}: {conn.status_label(status)} — {res.detail}"
    facts = scrub(dict(res.facts or {}))
    if facts:
        out += f"\n  сведения: {json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)}"
    if ctx.notes:
        out += "".join(f"\n  · {n['text']}" for n in ctx.notes)
    if code == "dgis":
        out += "\n  (запрос не учтён в счётчике квоты)"
    if code == "anthropic":
        out += "\n  (только сведения о модели — без генерации и без оплаты)"
    return out


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.integrations.smoke",
                                description="Живая проверка подключений (без сохранения публикаций)")
    p.add_argument("codes", nargs="*", help=f"коды подключений: {', '.join(registry.MODULES)} (по умолчанию — все)")
    args = p.parse_args(argv)
    logs.install()
    codes = args.codes or list(registry.CONNECTORS)
    unknown = [c for c in codes if c not in registry.CONNECTORS]
    if unknown:
        _line(f"Неизвестные подключения: {', '.join(unknown)}. Есть: {', '.join(registry.CONNECTORS)}")
        return 2
    for code in codes:
        _line(check_one(code))
    return 0


if __name__ == "__main__":
    sys.exit(main())
