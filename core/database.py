from flask import Flask

from .extensions import db
from .seed import seed_data_if_empty
from ..migrations.run_migrations import run_migrations


def init_db(app: Flask) -> None:
    db.init_app(app)

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        try:
            db.session.remove()
        except Exception:
            pass

    @app.before_request
    def close_stale_session():
        try:
            db.session.remove()
        except Exception:
            pass

    with app.app_context():
        db.create_all()
        if app.config.get('RUN_MIGRATIONS', False):
            run_migrations()
        if app.config.get('RUN_SEED', False):
            seed_data_if_empty(app)
