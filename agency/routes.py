from flask import Blueprint, jsonify, request
from flasgger import swag_from

from ..middleware.auth_middleware import token_required
from . import AgencyError, create_bulk_booking, get_agency_bookings

bp = Blueprint('agency', __name__)


@bp.route('/api/agency/bulk-booking', methods=['POST'])
@token_required(msg='Login required')
@swag_from({
    'tags': ['Agency'],
    'summary': 'Create a bulk booking',
    'description': 'Book 5 or more seats in a single order.',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'route_id': {'type': 'integer', 'example': 1},
                    'seat_numbers': {
                        'type': 'array', 'items': {'type': 'integer'},
                        'example': [1, 2, 3, 4, 5]
                    },
                    'passenger_name': {'type': 'string'},
                    'passenger_email': {'type': 'string'},
                    'passenger_phone': {'type': 'string'},
                    'special_requests': {'type': 'string'},
                },
                'required': ['route_id', 'seat_numbers']
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Bulk booking initiated',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string'},
                    'booking_id': {'type': 'integer'},
                    'checkout_url': {'type': 'string'},
                    'total_seats': {'type': 'integer'},
                    'amount': {'type': 'string'},
                }
            }
        },
        400: {'description': 'Bad request or less than 5 seats'},
        403: {'description': 'Agency access required'},
    }
})
def bulk_booking(current_user):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = create_bulk_booking(current_user, data)
        return jsonify(result), 200
    except AgencyError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/agency/bookings', methods=['GET'])
@token_required(msg='Login required')
@swag_from({
    'tags': ['Agency'],
    'summary': 'List all bookings for the logged-in agency',
    'description': 'Returns all bookings made by the authenticated agency account.',
    'security': [{'BearerAuth': []}],
    'responses': {
        200: {
            'description': 'List of agency bookings',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string'},
                    'bookings': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'booking_id': {'type': 'integer'},
                                'total_seats': {'type': 'integer'},
                                'status': {'type': 'string'},
                                'payment_status': {'type': 'string'},
                            }
                        }
                    }
                }
            }
        },
        403: {'description': 'Agency access required'},
    }
})
def agency_bookings(current_user):
    try:
        result = get_agency_bookings(current_user)
        return jsonify(result), 200
    except AgencyError as e:
        return jsonify({'message': str(e)}), e.status_code
