import json as json_lib

from flask import request as flask_request

from ..common.extensions import db
from ..common.models import AuditLog


def format_response(response):
    if not response.content_type or 'application/json' not in response.content_type:
        return response

    try:
        data = response.get_json(silent=True) or {}
    except Exception:
        return response

    if 'swagger' in response.content_type.lower() or '/apidocs/' in flask_request.path or '/apispec.json' in flask_request.path:
        return response

    description = data.pop('message', data.pop('description', ''))
    metadata = {k: v for k, v in data.items() if k not in ('code', 'description')}

    formatted = {
        'code': response.status_code,
        'description': description,
        'metadata': metadata
    }

    response.set_data(json_lib.dumps(formatted, default=str))
    response.content_length = len(response.get_data())
    response.content_type = 'application/json'
    return response
