from datetime import datetime

from flask import g, request

from ..common.auth_utils import _extract_bearer_token, decode_token
from ..common.extensions import db
from ..common.models import AuditLog


def get_current_user_id():
    token = _extract_bearer_token()
    if not token:
        return None
    try:
        claims = decode_token(token)
        return claims.get('user_id')
    except Exception:
        return None


def capture_request():
    body = request.get_data(as_text=True)
    g.audit_request_body = body[:5000] if body else None
    g.audit_start = datetime.utcnow()
    g.audit_user_id = get_current_user_id()


def log_response(response):
    try:
        resp_body = response.get_data(as_text=True)
        status = response.status_code

        log = AuditLog(
            method=request.method,
            path=request.path,
            query_string=request.query_string.decode('utf-8')[:2000] if request.query_string else None,
            request_body=getattr(g, 'audit_request_body', None),
            user_id=getattr(g, 'audit_user_id', None),
            ip_address=request.remote_addr,
            response_status=status,
            response_body=resp_body[:10000] if resp_body else None,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()
    return response
