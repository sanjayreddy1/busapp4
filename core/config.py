import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / '.env'
dev_env_path = BASE_DIR / '.env.dev'

if env_path.exists():
    load_dotenv(env_path, override=True)
elif dev_env_path.exists():
    load_dotenv(dev_env_path, override=True)


def _get_env(key, default):
    val = os.environ.get(key)
    if val:
        return val
    if env_path.exists():
        m = re.search(rf'^{key}\s*=\s*(.+)', env_path.read_text(), re.M)
        if m:
            return m.group(1).strip()
    if dev_env_path.exists():
        m = re.search(rf'^{key}\s*=\s*(.+)', dev_env_path.read_text(), re.M)
        if m:
            return m.group(1).strip()
    return default


class Config:
    SECRET_KEY = _get_env('SECRET_KEY', 'your-secret-key-change-this')
    SQLALCHEMY_DATABASE_URI = _get_env(
        'DATABASE_URI',
        'postgresql://postgres:postgres@localhost:5432/bus_booking'
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    RUN_MIGRATIONS = _get_env('RUN_MIGRATIONS', 'False').lower() == 'true'
    RUN_SEED = _get_env('RUN_SEED', 'False').lower() == 'true'

    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_size': 5,
        'max_overflow': 10,
        'pool_recycle': 300,
    }

    STRIPE_API_KEY = _get_env('STRIPE_API_KEY', 'sk_test_your_stripe_secret_key')
    STRIPE_WEBHOOK_SECRET = _get_env('STRIPE_WEBHOOK_SECRET', 'whsec_your_webhook_secret')

    PAYMENT_SUCCESS_URL = _get_env('PAYMENT_SUCCESS_URL', 'http://localhost:5000/api/payment/success')
    PAYMENT_CANCEL_URL = _get_env('PAYMENT_CANCEL_URL', 'http://localhost:5000/api/payment/cancel')
    STRIPE_PAYMENT_LINK = _get_env('STRIPE_PAYMENT_LINK', 'https://buy.stripe.com/test_cNi14f2ZJ70X2i31IwfEk00')
    FRONTEND_URL = _get_env('FRONTEND_URL', 'http://localhost:3000')

