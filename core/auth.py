import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, request
from jwcrypto import jwk
from jwcrypto import jwt as jwcrypto_jwt
from jwcrypto.jws import InvalidJWSSignature


def validate_indian_phone(phone: str) -> bool:
    pattern = r'^[6-9]\d{9}$|^\+91[6-9]\d{9}$|^0[6-9]\d{9}$'
    return re.match(pattern, str(phone)) is not None


def format_indian_phone(phone: str) -> str:
    phone = str(phone)
    phone = re.sub(r'\D', '', phone)
    if phone.startswith('91'):
        phone = phone[2:]
    elif phone.startswith('0'):
        phone = phone[1:]
    return f"+91{phone}"


def _make_key():
    import base64
    import hashlib
    key_bytes = hashlib.sha512(current_app.config['SECRET_KEY'].encode('utf-8')).digest()
    k = base64.urlsafe_b64encode(key_bytes).decode('utf-8')
    return jwk.JWK(kty='oct', k=k)


def generate_token(user_id, role, expiry_minutes=45):
    key = _make_key()
    now = datetime.now(timezone.utc)
    jwttoken = jwcrypto_jwt.JWT(header={"alg": "HS512"}, claims={
        'user_id': user_id,
        'role': role,
        'exp': int((now + timedelta(minutes=expiry_minutes)).timestamp()),
        'iat': int(now.timestamp()),
        'jti': secrets.token_urlsafe(32),
    })
    jwttoken.make_signed_token(key)
    return jwttoken.serialize()


def _make_old_key():
    import base64
    import hashlib
    key_bytes = hashlib.sha256(current_app.config['SECRET_KEY'].encode('utf-8')).digest()
    k = base64.urlsafe_b64encode(key_bytes).decode('utf-8')
    return jwk.JWK(kty='oct', k=k)


def decode_token(token):
    for make_key in (_make_key, _make_old_key):
        key = make_key()
        try:
            verified = jwcrypto_jwt.JWT(key=key, jwt=token)
            return json.loads(verified.claims)
        except Exception:
            continue
    raise InvalidJWSSignature('Verification failed for all signatures')


def _extract_bearer_token():
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None
    parts = auth_header.split(' ')
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None
    return parts[1]


def verify_bearer_token():
    token = _extract_bearer_token()
    if not token:
        return False, 'Authorization token is missing or invalid. Provide a valid Bearer token', 401

    try:
        claims = decode_token(token)
    except jwcrypto_jwt.JWTExpired:
        return False, 'Token has expired. Please login again', 401
    except (InvalidJWSSignature, Exception):
        return False, 'Token is invalid. Provide a valid Bearer token', 401

    user_id = claims.get('user_id')
    role = claims.get('role')

    return True, {'user_id': user_id, 'role': role}
