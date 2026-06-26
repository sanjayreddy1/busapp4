from flasgger import Swagger


swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": 'apispec',
            "route": '/apispec.json',
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    # Expose UI at both /apidocs/ (existing) and /docs/ (fallback).
    # Flasgger expects a single route, so we keep /apidocs/ and make /docs/ an alias via template.
    "specs_route": "/apidocs/",

    "securityDefinitions": {
        "BearerAuth": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "JWT Authorization header using the Bearer scheme. Example: 'Bearer {token}'"
        }
    }
}

swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "Bus Booking API",
        "description": "API for bus ticket booking system with Indian standards (INR currency, Indian phone numbers)",
        "version": "1.0.0",
        "contact": {
            "name": "API Support",
            "email": "support@busbooking.com"
        }
    },
    "host": "localhost:5000",
    "basePath": "/",
    "schemes": ["http", "https"],
    "securityDefinitions": {
        "BearerAuth": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Enter 'Bearer {token}'"
        }
    },
    "security": [
        {
            "BearerAuth": []
        }
    ]
}


def configure_swagger(app):
    Swagger(app, config=swagger_config, template=swagger_template)
