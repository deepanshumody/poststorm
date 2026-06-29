import json
from datetime import datetime, timezone

from backend.ledger import service
from backend.ledger.models import Feedback, PostedLine, ReviewException
from backend.ledger.service import line_key
from backend.schema import LineItem


def _now():
    return datetime.now(timezone.utc)


def review_queue(session, tenant_id, status="open"):
    rows = (session.query(ReviewException)
            .filter_by(tenant_id=tenant_id, status=status)
            .order_by(ReviewException.id.desc()))
    out = []
    for ex in rows:
        p = json.loads(ex.payload)
        out.append({"id": ex.id, "kind": ex.kind, "status": ex.status,
                    "line": p.get("line"), "candidates": p.get("candidates", []),
                    "reason": p.get("reason")})
    return out


def resolve(session, tenant_id, exc_id, action, corrected=None, chosen_claim=None,
            reviewer="demo-reviewer"):
    ex = session.get(ReviewException, exc_id)
    if ex is None or ex.tenant_id != tenant_id:
        raise ValueError("exception not found")
    if ex.status != "open":
        return {"status": ex.status, "posted": False, "event_id": None, "noop": True}

    line = LineItem(**json.loads(ex.payload)["line"])
    event_id = None

    if action == "dismiss":
        ex.status = "dismissed"
    elif action in ("approve", "pick", "correct"):
        post_line = line
        if action == "pick" and not chosen_claim:
            raise ValueError("pick requires chosen_claim")
        if action == "correct":
            if not corrected:
                raise ValueError("correct requires corrected fields")
            post_line = line.model_copy(update=corrected)
            session.add(Feedback(
                tenant_id=tenant_id, kind=ex.kind,
                original_line=json.dumps(line.model_dump(mode="json")),
                corrected_line=json.dumps(post_line.model_dump(mode="json")), reviewer=reviewer))
        # Remove the placeholder PostedLine (event_id=None) created by _exception
        # so that post_reviewed_line can create a real one with an event.
        lk = line_key(tenant_id, post_line)
        placeholder = session.query(PostedLine).filter_by(tenant_id=tenant_id, line_key=lk).first()
        if placeholder and placeholder.event_id is None:
            session.delete(placeholder)
            session.flush()
        event_id = service.post_reviewed_line(
            session, tenant_id, "review", post_line, service._is_recoup(post_line), chosen_claim, reviewer)
        ex.status = "resolved"
    else:
        raise ValueError(f"unknown action: {action}")

    ex.resolved_by = reviewer
    ex.resolved_at = _now()
    ex.resolution = json.dumps({"action": action, "chosen_claim": chosen_claim, "corrected": corrected})
    session.commit()
    return {"status": ex.status, "posted": event_id is not None, "event_id": event_id}


def feedback_list(session, tenant_id):
    return [{"id": f.id, "kind": f.kind, "reviewer": f.reviewer,
             "original": json.loads(f.original_line), "corrected": json.loads(f.corrected_line)}
            for f in (session.query(Feedback).filter_by(tenant_id=tenant_id)
                      .order_by(Feedback.id.desc()))]
