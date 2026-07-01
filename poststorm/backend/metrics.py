from sqlalchemy import func

from backend.eval import run as eval_run
from backend.ingest.models import Document, IngestJob
from backend.ledger.models import Account, Event, ReviewException
from backend.writeback.models import Delivery


def _gauge(lines, name, help_, value):
    lines.append(f"# HELP {name} {help_}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name} {value}")


def _labeled(lines, name, help_, rows):
    lines.append(f"# HELP {name} {help_}")
    lines.append(f"# TYPE {name} gauge")
    for status, count in rows:
        lines.append(f'{name}{{status="{status}"}} {count}')


def render_metrics(session) -> str:
    lines: list[str] = []
    try:
        _gauge(lines, "poststorm_ledger_events", "Total ledger events", session.query(Event).count())
    except Exception:
        pass
    try:
        dump = session.query(func.coalesce(func.sum(Account.balance_cents), 0)).filter_by(
            type="dump_account").scalar() or 0
        _gauge(lines, "poststorm_dump_exposure_cents", "Parked dump-account exposure (cents)", dump)
    except Exception:
        pass
    for name, help_, model in [
        ("poststorm_review_exceptions", "Review exceptions by status", ReviewException),
        ("poststorm_deliveries", "Deliveries by status", Delivery),
        ("poststorm_ingest_jobs", "Ingest jobs by status", IngestJob),
        ("poststorm_documents", "Documents by status", Document),
    ]:
        try:
            rows = session.query(model.status, func.count()).group_by(model.status).all()
            if rows:
                _labeled(lines, name, help_, rows)
        except Exception:
            pass
    try:
        report = eval_run.read_report()
        if report:
            rec = report.get("recoup", {})
            for name, val in [
                ("poststorm_field_accuracy", report.get("field_accuracy", {}).get("overall")),
                ("poststorm_recoup_precision", rec.get("precision")),
                ("poststorm_recoup_recall", rec.get("recall")),
                ("poststorm_recoup_f1", rec.get("f1")),
                ("poststorm_eval_docs", report.get("docs")),
            ]:
                if val is not None:
                    _gauge(lines, name, f"Latest eval: {name}", val)
    except Exception:
        pass
    return "\n".join(lines) + "\n"
