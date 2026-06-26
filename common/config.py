import os
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / '.env'
dev_env_path = Path(__file__).resolve().parent.parent / '.env.dev'

if env_path.exists():
    load_dotenv(env_path)
elif dev_env_path.exists():
    load_dotenv(dev_env_path)

_base_dir = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key-change-this')
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URI',
        f"sqlite:///{_base_dir / 'instance' / 'bus_booking.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    STRIPE_API_KEY = os.getenv('STRIPE_API_KEY', 'sk_test_your_stripe_secret_key')
    STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', 'whsec_your_webhook_secret')

    PAYMENT_SUCCESS_URL = os.getenv('PAYMENT_SUCCESS_URL', 'http://localhost:5000/api/payment/success')
    PAYMENT_CANCEL_URL = os.getenv('PAYMENT_CANCEL_URL', 'http://localhost:5000/api/payment/cancel')
    STRIPE_PAYMENT_LINK = os.getenv('STRIPE_PAYMENT_LINK', 'https://buy.stripe.com/test_cNi14f2ZJ70X2i31IwfEk00')
