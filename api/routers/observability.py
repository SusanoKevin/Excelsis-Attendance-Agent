from fastapi import APIRouter, Depends

from api.deps import require_admin
from src.observability.audit import recent_admin_actions
from src.observability.trace import get_recorder
from src.security import UserContext

router = APIRouter()


@router.get("/traces")
def list_traces(limit: int = 50, _: UserContext = Depends(require_admin)):
    """Most recent agent run traces (admin-only) — surfaces per-tool-call
    timing, repeated-tool-call (loop) flags, and error status for debugging
    reliability issues without needing to grep application logs."""
    recorder = get_recorder()
    return [t.to_dict() for t in recorder.recent(limit=limit)]


@router.get("/admin-audit")
def list_admin_audit(limit: int = 100, _: UserContext = Depends(require_admin)):
    """Most recent admin actions (admin-only) — who created/deleted which
    account, when, and from where. FERPA-style access record for the admin
    plane; state-changing user-management actions are recorded here."""
    return recent_admin_actions(limit=limit)
