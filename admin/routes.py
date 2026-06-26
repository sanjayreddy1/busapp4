from flask import Blueprint, jsonify, request
from flasgger import swag_from

from ..middleware.auth_middleware import admin_required, token_required
from . import AdminError
from . import (
    get_dashboard_stats,
    get_all_tickets,
    manage_routes_get,
    manage_routes_create,
    create_bus,
    patch_bus,
    patch_route,
    update_ticket_status,
    update_payment_status,
    get_all_payments_admin,
    get_payment_detail_admin,
    get_all_users_admin,
    get_all_drivers_admin,
    update_driver_admin,
    get_all_agencies_admin,
    register_agency_admin,
    get_bus_occupancy,
    export_bus_document,
    get_agency_tickets_admin,
)


bp = Blueprint('admin', __name__)


@bp.route('/api/admin/dashboard/stats', methods=['GET'])
@token_required(msg='Admin login required to view dashboard')
@admin_required
@swag_from({
    'tags': ['Admin'],
    'summary': 'Get dashboard statistics',
    'responses': {
        200: {
            'description': 'Dashboard stats retrieved successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Dashboard stats retrieved successfully'},
                    'statistics': {'type': 'object'},
                    'route_statistics': {'type': 'array'},
                    'recent_bookings': {'type': 'array'}
                }
            }
        },
        400: {'description': 'Bad request'},
        500: {'description': 'Internal server error'}
    }
})
def admin_dashboard_stats(current_user):
    try:
        result = get_dashboard_stats()
        return jsonify(result), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/drivers', methods=['GET', 'PATCH'])
@token_required(msg='Admin login required')
@admin_required
def manage_drivers(current_user):
    try:
        if request.method == 'GET':
            result = get_all_drivers_admin()
            return jsonify(result), 200
        else:
            body = request.get_json(silent=True) or {}
            data = {**dict(request.args), **body}
            driver_id = data.get('driver_id')
            if not driver_id:
                return jsonify({'message': 'driver_id is required'}), 400
            result = update_driver_admin(driver_id, data)
            return jsonify(result), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/agencies', methods=['GET', 'POST'])
@token_required(msg='Admin login required')
@admin_required
def manage_agencies(current_user):
    try:
        if request.method == 'GET':
            result = get_all_agencies_admin()
            return jsonify(result), 200
        else:
            body = request.get_json(silent=True) or {}
            data = {**dict(request.args), **body}
            result = register_agency_admin(data)
            return jsonify(result), 201
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/agencies/<int:agency_id>/tickets', methods=['GET'])
@token_required(msg='Admin login required')
@admin_required
def agency_tickets(current_user, agency_id):
    try:
        result = get_agency_tickets_admin(agency_id)
        return jsonify(result), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/buses/<int:bus_id>/occupancy', methods=['GET'])
@token_required(msg='Admin login required')
@admin_required
def bus_occupancy(current_user, bus_id):
    try:
        result = get_bus_occupancy(bus_id)
        return jsonify(result), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/buses/<int:bus_id>/export/<fmt>', methods=['POST'])
@token_required(msg='Admin login required')
@admin_required
def export_bus(current_user, bus_id, fmt):
    try:
        result = export_bus_document(bus_id, fmt)
        return jsonify(result), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/export/download/<base64_id>', methods=['GET'])
@token_required(msg='Admin login required')
@admin_required
def download_export(current_user, base64_id):
    try:
        from ..core.models import BusExportDocument
        doc = BusExportDocument.query.filter_by(base64_id=base64_id).first()
        if not doc:
            return jsonify({'message': 'Document not found'}), 404
        from flask import make_response
        content_types = {'pdf': 'application/pdf', 'csv': 'text/csv', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
        ext = doc.format
        response = make_response(doc.pdf_blob)
        response.headers['Content-Type'] = content_types.get(ext, 'application/octet-stream')
        response.headers['Content-Disposition'] = f'attachment; filename=bus_{doc.bus_id}_export.{ext}'
        return response
    except Exception as e:
        return jsonify({'message': f'Download failed: {str(e)}'}), 500



@bp.route('/api/admin/tickets', methods=['GET'])
@token_required(msg='Admin login required to view tickets')
@admin_required
def admin_get_all_tickets(current_user):
    try:
        status = request.args.get('status')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        source = request.args.get('source')
        destination = request.args.get('destination')
        result = get_all_tickets(status, date_from, date_to, source, destination)
        return jsonify(result), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/tickets/<int:ticket_id>', methods=['PATCH'])
@token_required(msg='Admin login required to update tickets')
@admin_required
@swag_from({
    'tags': ['Admin'],
    'summary': 'Update ticket status/details',
    'description': 'Update ticket status or passenger/special request fields',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'ticket_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1},
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'cancelled'},
                    'special_requests': {'type': 'string', 'example': 'Window seat preferred'},
                    'passenger_name': {'type': 'string', 'example': 'John Doe'},
                    'passenger_email': {'type': 'string', 'example': 'john@example.com'},
                    'passenger_phone': {'type': 'string', 'example': '9876543210'}
                }
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Ticket updated successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Ticket updated successfully'},
                    'ticket': {'type': 'object'}
                }
            }
        },
        400: {'description': 'Bad request'},
        404: {'description': 'Ticket not found'}
    }
})
def update_ticket(current_user, ticket_id):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = update_ticket_status(ticket_id, data)
        return jsonify({'message': 'Ticket updated successfully', 'ticket': result}), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/routes', methods=['GET', 'POST'])
@token_required(msg='Admin login required to manage routes')
@admin_required
@swag_from({
    'tags': ['Admin'],
    'summary': 'Manage routes (list or create)',
    'description': 'GET lists all routes. POST creates a route',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': False,
            'schema': {
                'type': 'object',
                'properties': {
                    'source': {'type': 'string', 'example': 'Chennai'},
                    'destination': {'type': 'string', 'example': 'Bangalore'},
                    'departure_time': {'type': 'string', 'example': '2026-06-20T09:00:00'},
                    'arrival_time': {'type': 'string', 'example': '2026-06-20T13:00:00'},
                    'bus_id': {'type': 'integer', 'example': 1},
                    'price': {'type': 'number', 'example': 675.00},
                    'status': {'type': 'string', 'example': 'active'},
                },
                'required': ['source', 'destination', 'departure_time', 'arrival_time', 'bus_id', 'price']
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Routes retrieved successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Routes retrieved successfully'},
                    'routes': {'type': 'array'}
                }
            }
        },
        201: {
            'description': 'Route created successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Route created successfully'},
                    'route_id': {'type': 'integer'}
                }
            }
        },
        400: {'description': 'Bad request'},
        404: {'description': 'Bus not found'}
    }
})
def manage_routes(current_user):
    try:
        if request.method == 'GET':
            result = manage_routes_get()
            return jsonify({'message': 'Routes retrieved successfully', 'routes': result}), 200
        else:
            body = request.get_json(silent=True) or {}
            data = {**dict(request.args), **body}
            result = manage_routes_create(data)
            return jsonify(result), 201
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/routes/<int:route_id>', methods=['PATCH'])
@token_required(msg='Admin login required to update routes')
@admin_required
@swag_from({
    'tags': ['Admin'],
    'summary': 'Patch route',
    'description': 'Update route details. If status is set to cancelled, it may cancel related confirmed bookings.',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'route_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1},
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'source': {'type': 'string', 'example': 'Chennai'},
                    'destination': {'type': 'string', 'example': 'Bangalore'},
                    'departure_time': {'type': 'string', 'example': '2026-06-20T09:00:00'},
                    'arrival_time': {'type': 'string', 'example': '2026-06-20T13:00:00'},
                    'price': {'type': 'number', 'example': 675.00},
                    'status': {'type': 'string', 'example': 'cancelled'},
                    'cancellation_reason': {'type': 'string', 'example': 'Road closure'}
                }
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Route updated successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Route updated successfully'},
                    'route': {'type': 'object'}
                }
            }
        },
        400: {'description': 'Bad request'},
        404: {'description': 'Route not found'}
    }
})
def patch_route_endpoint(current_user, route_id):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = patch_route(route_id, data)
        return jsonify({'message': 'Route updated successfully', 'route': result}), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/users', methods=['GET'])
@token_required(msg='Admin login required to view users')
@admin_required
@swag_from({
    'tags': ['Admin'],
    'summary': 'Get all users',
    'parameters': [
        {'name': 'role', 'in': 'query', 'type': 'string', 'example': 'user'},
    ],
    'responses': {
        200: {
            'description': 'Users retrieved successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string'},
                    'total_users': {'type': 'integer'},
                    'users': {'type': 'array'}
                }
            }
        },
        500: {'description': 'Internal server error'}
    }
})
def get_all_users_endpoint(current_user):
    try:
        role = request.args.get('role')
        result = get_all_users_admin(role)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'message': f'Failed to fetch users: {str(e)}'}), 500


@bp.route('/api/admin/buses', methods=['POST'])
@token_required(msg='Admin login required to create buses')
@admin_required
def create_bus_endpoint(current_user):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = create_bus(data)
        return jsonify(result), 201
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/admin/buses/<int:bus_id>', methods=['PATCH'])
@token_required(msg='Admin login required to update buses')
@admin_required
@swag_from({
    'tags': ['Admin'],
    'summary': 'Patch bus',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'bus_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1},
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'bus_number': {'type': 'string', 'example': 'TN-01-AB-1234'},
                    'total_seats': {'type': 'integer', 'example': 40},
                    'amenities': {'type': 'string', 'example': 'AC, TV'},
                    'bus_type': {'type': 'string', 'example': 'sleeper'},
                    'is_active': {'type': 'boolean', 'example': True}
                }
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Bus updated successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Bus updated successfully'},
                    'bus': {'type': 'object'}
                }
            }
        },
        400: {'description': 'Bad request'},
        404: {'description': 'Bus not found'}
    }
})
def patch_bus_endpoint(current_user, bus_id):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = patch_bus(bus_id, data)
        return jsonify({'message': 'Bus updated successfully', 'bus': result}), 200
    except AdminError as e:
        return jsonify({'message': str(e)}), e.status_code
