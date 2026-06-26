from flask import Blueprint, jsonify, request
from flasgger import swag_from

from ..middleware.auth_middleware import driver_required, token_required
from . import DriverError, get_assigned_routes, update_route_status

bp = Blueprint('driver', __name__)


@bp.route('/api/driver/routes', methods=['GET'])
@token_required(msg='Driver login required')
@driver_required
@swag_from({
    'tags': ['Driver'],
    'summary': 'Get assigned routes for the logged-in driver',
    'description': 'Returns all routes assigned to the authenticated driver via route.driver_id',
    'security': [{'BearerAuth': []}],
    'responses': {
        200: {
            'description': 'List of assigned routes',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string'},
                    'routes': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {'type': 'integer'},
                                'source': {'type': 'string'},
                                'destination': {'type': 'string'},
                                'departure_time': {'type': 'string'},
                                'arrival_time': {'type': 'string'},
                                'bus_number': {'type': 'string'},
                                'status': {'type': 'string'},
                            }
                        }
                    }
                }
            }
        },
        403: {'description': 'Driver access required'},
    }
})
def assigned_routes(current_user):
    try:
        result = get_assigned_routes(current_user)
        return jsonify(result), 200
    except DriverError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/driver/routes/<int:route_id>/status', methods=['PATCH'])
@token_required(msg='Driver login required')
@driver_required
@swag_from({
    'tags': ['Driver'],
    'summary': 'Update route status (driver)',
    'description': 'Update the status of an assigned route. Valid statuses: active, cancelled, completed.',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'route_id', 'in': 'path', 'type': 'integer', 'required': True},
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'enum': ['active', 'cancelled', 'completed'], 'example': 'completed'},
                    'cancellation_reason': {'type': 'string', 'example': 'Road blocked'}
                },
                'required': ['status']
            }
        }
    ],
    'responses': {
        200: {'description': 'Route status updated'},
        403: {'description': 'Unauthorized or driver access required'},
        404: {'description': 'Route not found'}
    }
})
def update_status(current_user, route_id):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = update_route_status(current_user, route_id, data)
        return jsonify(result), 200
    except DriverError as e:
        return jsonify({'message': str(e)}), e.status_code
