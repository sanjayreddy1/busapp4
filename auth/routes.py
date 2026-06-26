from flask import Blueprint, jsonify, request
from flasgger import swag_from

from . import AuthError, register_user, login_user, register_admin, register_driver, register_agency


bp = Blueprint('auth', __name__)


@bp.route('/api/register', methods=['GET', 'POST'])
@swag_from({
    'tags': ['Authentication'],
    'summary': 'Register a new user',
    'description': 'Create a new user account. Name, password, phone, gender, and date of birth are required; email is optional.',
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'example': 'John Doe'},
                    'email': {'type': 'string', 'example': 'user@example.com'},
                    'password': {'type': 'string', 'example': 'password123'},
                    'phone': {'type': 'string', 'example': '9876543210'},
                    'gender': {'type': 'string', 'example': 'male', 'description': 'male, female, or other'},
                    'dob': {'type': 'string', 'format': 'date', 'example': '1990-01-01'},
                    'location': {'type': 'string', 'example': 'Chennai, Tamil Nadu'},
                    'has_disability': {'type': 'boolean', 'example': False},
                    'disability_details': {'type': 'string', 'example': ''},
                    'emergency_contact': {'type': 'string', 'example': '9876543211'},
                    'profile_picture': {'type': 'string', 'example': 'https://example.com/photo.jpg'}
                },
                'required': ['name', 'password', 'phone', 'gender', 'dob']
            }
        }
    ],
    'responses': {
        201: {
            'description': 'User created successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'User created successfully'},
                    'user_id': {'type': 'integer'},
                    'phone': {'type': 'string'},
                    'gender': {'type': 'string'},
                    'email': {'type': 'string'}
                }
            }
        },
        400: {'description': 'Missing fields or invalid data'},
        409: {'description': 'User already exists'}
    }
})
def register():
    if request.method == 'GET':
        return jsonify({'error': 'wrong_method', 'message': 'Send a POST request with JSON body containing name, phone, gender, dob, password (email optional) to register'}), 200
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = register_user(data)
        return jsonify(result), 201
    except AuthError as e:
        return jsonify({'message': str(e)}), e.status_code
    except Exception as e:
        return jsonify({'message': 'An unexpected error occurred'}), 500


@bp.route('/api/login', methods=['GET', 'POST'])
@swag_from({
    'tags': ['Authentication'],
    'summary': 'Login user',
    'description': 'Authenticate user by email OR phone number and return JWT token',
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'email': {'type': 'string', 'example': 'user@example.com', 'description': 'Email or phone number'},
                    'phone': {'type': 'string', 'example': '9876543210', 'description': 'Email or phone number'},
                    'password': {'type': 'string', 'example': 'password123'}
                },
                'required': ['password']
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Login successful',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Login successful'},
                    'token': {'type': 'string'},
                    'user': {'type': 'object'}
                }
            }
        },
        401: {'description': 'Invalid credentials'}
    }
})
def login():
    if request.method == 'GET':
        return jsonify({'error': 'wrong_method', 'message': 'Send a POST request with JSON body containing email (or phone) and password to login'}), 200
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = login_user(data)
        return jsonify(result), 200
    except AuthError as e:
        return jsonify({'message': str(e)}), e.status_code
    except Exception as e:
        return jsonify({'message': 'An unexpected error occurred'}), 500


@bp.route('/api/admin/register', methods=['GET', 'POST'])
@swag_from({
    'tags': ['Authentication'],
    'summary': 'Register admin user',
    'description': 'Create a new admin account. Name, password, phone, gender, and dob are required; email is optional.',
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'example': 'Admin User'},
                    'email': {'type': 'string', 'example': 'admin@example.com'},
                    'password': {'type': 'string', 'example': 'admin123'},
                    'phone': {'type': 'string', 'example': '9876543210'},
                    'gender': {'type': 'string', 'example': 'male', 'description': 'male, female, or other'},
                    'dob': {'type': 'string', 'format': 'date', 'example': '1990-01-01'},
                    'location': {'type': 'string', 'example': 'Chennai, Tamil Nadu'},
                    'has_disability': {'type': 'boolean', 'example': False},
                    'disability_details': {'type': 'string', 'example': ''},
                    'emergency_contact': {'type': 'string', 'example': '9876543211'}
                },
                'required': ['name', 'password', 'phone', 'gender', 'dob']
            }
        }
    ],
    'responses': {
        201: {
            'description': 'Admin created successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Admin created successfully'},
                    'admin_id': {'type': 'integer'}
                }
            }
        },
        400: {'description': 'Missing fields or invalid data'},
        409: {'description': 'Admin already exists'}
    }
})
def register_admin_endpoint():
    if request.method == 'GET':
        return jsonify({'error': 'wrong_method', 'message': 'Send a POST request with JSON body containing name, email, and password to register as admin'}), 200
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = register_admin(data)
        return jsonify(result), 201
    except AuthError as e:
        return jsonify({'message': str(e)}), e.status_code
    except Exception as e:
        return jsonify({'message': 'An unexpected error occurred'}), 500


@bp.route('/api/driver/register', methods=['GET', 'POST'])
@swag_from({
    'tags': ['Driver'],
    'summary': 'Register a new driver',
    'description': 'Create a new driver account. Name, password, phone, gender, and dob are required.',
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'example': 'Driver Name'},
                    'email': {'type': 'string', 'example': 'driver@example.com'},
                    'password': {'type': 'string', 'example': 'password123'},
                    'phone': {'type': 'string', 'example': '9876543210'},
                    'gender': {'type': 'string', 'example': 'male'},
                    'dob': {'type': 'string', 'format': 'date', 'example': '1990-01-01'},
                },
                'required': ['name', 'password', 'phone', 'gender', 'dob']
            }
        }
    ],
    'responses': {
        201: {'description': 'Driver registered'},
        400: {'description': 'Missing fields or invalid data'},
        409: {'description': 'Already exists'}
    }
})
def register_driver_endpoint():
    if request.method == 'GET':
        return jsonify({'error': 'wrong_method', 'message': 'Send a POST request to register as driver'}), 200
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = register_driver(data)
        return jsonify(result), 201
    except AuthError as e:
        return jsonify({'message': str(e)}), e.status_code
    except Exception as e:
        return jsonify({'message': 'An unexpected error occurred'}), 500


@bp.route('/api/agency/register', methods=['GET', 'POST'])
@swag_from({
    'tags': ['Agency'],
    'summary': 'Register a new bus agency',
    'description': 'Create a new bus agency account. Name, password, phone, gender, and dob are required.',
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'example': 'Agency Name'},
                    'email': {'type': 'string', 'example': 'agency@example.com'},
                    'password': {'type': 'string', 'example': 'password123'},
                    'phone': {'type': 'string', 'example': '9876543210'},
                    'gender': {'type': 'string', 'example': 'female'},
                    'dob': {'type': 'string', 'format': 'date', 'example': '1985-05-15'},
                },
                'required': ['name', 'password', 'phone', 'gender', 'dob']
            }
        }
    ],
    'responses': {
        201: {'description': 'Agency registered'},
        400: {'description': 'Missing fields or invalid data'},
        409: {'description': 'Already exists'}
    }
})
def register_agency_endpoint():
    if request.method == 'GET':
        return jsonify({'error': 'wrong_method', 'message': 'Send a POST request to register as bus agency'}), 200
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = register_agency(data)
        return jsonify(result), 201
    except AuthError as e:
        return jsonify({'message': str(e)}), e.status_code
    except Exception as e:
        return jsonify({'message': 'An unexpected error occurred'}), 500
