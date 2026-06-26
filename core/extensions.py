from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import stripe

from .config import Config

db = SQLAlchemy()
bcrypt = Bcrypt()
stripe.api_key = Config.STRIPE_API_KEY
