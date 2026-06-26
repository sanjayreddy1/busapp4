from flask import jsonify

from .config import Config
from .extensions import bcrypt, db, stripe
from .seed import seed_data_if_empty
from .swagger import configure_swagger


def init_app(app):
    app.config.from_object(Config)

    from flask_cors import CORS
    CORS(app)

    configure_swagger(app)

    db.init_app(app)
    bcrypt.init_app(app)
    stripe.api_key = app.config['STRIPE_API_KEY']

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'message': 'Resource not found'}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({'message': 'Method not allowed'}), 405

    @app.errorhandler(500)
    def internal_server_error(e):
        return jsonify({'message': 'Internal server error'}), 500

    with app.app_context():
        db.create_all()
        seed_data_if_empty(app)
