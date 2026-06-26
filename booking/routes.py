import io
import json

from flask import Blueprint, jsonify, make_response, request
from flasgger import swag_from
from xhtml2pdf import pisa

from ..booking.service import _build_ticket_data, build_ticket_html, build_premium_ticket_html
from ..core.models import Booking, Ticket, TicketDocument
from ..middleware.auth_middleware import admin_required, driver_required, token_required
from . import BookingError
from . import (
    cancel_booking as cancel_booking_ctrl,
    confirm_booking as confirm_booking_ctrl,
    get_user_bookings as get_user_bookings_ctrl,
    handle_stripe_webhook,
    initiate_booking as initiate_booking_ctrl,
    lock_seats as lock_seats_ctrl,
    release_seats as release_seats_ctrl,
    get_ticket_data as get_ticket_data_ctrl,
    get_bus_manifest as get_bus_manifest_ctrl,
    update_booking as update_booking_ctrl,
)


bp = Blueprint('booking', __name__)


@bp.route('/api/booking/initiate', methods=['POST'])
@token_required(msg='Please log in to make a booking')
@swag_from({
    'tags': ['Booking'],
    'summary': 'Initiate booking',
    'description': 'Create a new booking and generate Stripe Checkout session. Total amount is server-calculated from route price and seat multipliers.',
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
                    'seat_numbers': {'type': 'array', 'items': {'type': 'integer'}, 'example': [5, 6]},
                    'note': {'type': 'string', 'example': 'seat_numbers is required to initiate booking'},
                    'passenger_name': {'type': 'string', 'example': 'John Doe'},
                    'passenger_email': {'type': 'string', 'example': 'john@example.com'},
                    'passenger_phone': {'type': 'string', 'example': '9876543210'},
                    'special_requests': {'type': 'string', 'example': 'Window seat preferred'},
                    'passengers': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'seat_number': {'type': 'integer', 'example': 5},
                                'name': {'type': 'string', 'example': 'John Doe'},
                                'age': {'type': 'integer', 'example': 30},
                                'gender': {'type': 'string', 'example': 'male'},
                                'phone': {'type': 'string', 'example': '9876543210'},
                                'email': {'type': 'string', 'example': 'john@example.com'}
                            }
                        },
                        'example': [{'seat_number': 5, 'name': 'John Doe', 'age': 30, 'gender': 'male'}]
                    }
                },
                'required': ['route_id', 'seat_numbers']
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Booking initiated successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Booking initiated successfully'},
                    'booking_id': {'type': 'integer'},
                    'checkout_url': {'type': 'string'},
                    'checkout_session_id': {'type': 'string'},
                    'amount': {'type': 'string'},
                    'amount_value': {'type': 'number'}
                }
            }
        },
        400: {'description': 'Bad request'},
        404: {'description': 'Route not found'}
    }
})
def initiate_booking(current_user):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}

        seat_ids = request.args.getlist('seat_id')
        if seat_ids:
            data['seat_numbers'] = [int(s) for s in seat_ids]

        result = initiate_booking_ctrl(current_user, data)
        return jsonify(result), 200
    except BookingError as e:
        resp = {'message': str(e)}
        if e.extra:
            resp.update(e.extra)
        return jsonify(resp), e.status_code


@bp.route('/api/booking/<int:booking_id>', methods=['PATCH'])
@token_required(msg='Please log in to update your booking')
@swag_from({
    'tags': ['Booking'],
    'summary': 'Update booking details',
    'description': 'Update passenger information and special requests for a pending booking',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'booking_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1},
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'passenger_name': {'type': 'string', 'example': 'John Doe'},
                    'passenger_email': {'type': 'string', 'example': 'john@example.com'},
                    'passenger_phone': {'type': 'string', 'example': '9876543210'},
                    'special_requests': {'type': 'string', 'example': 'Window seat preferred'}
                }
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Booking updated successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Booking updated successfully'},
                    'booking': {'type': 'object'}
                }
            }
        },
        400: {'description': 'Bad request'},
        403: {'description': 'Unauthorized'},
        404: {'description': 'Booking not found'}
    }
})
def update_booking(current_user, booking_id):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = update_booking_ctrl(current_user, booking_id, data)
        return jsonify({'message': 'Booking updated successfully', 'booking': result}), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/booking/confirm', methods=['POST'])
@token_required(msg='Please log in to confirm your booking')
@swag_from({
    'tags': ['Booking'],
    'summary': 'Confirm booking after payment',
    'description': 'Confirm booking after successful Stripe Checkout payment',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'session_id': {'type': 'string', 'example': 'cs_test_a1b2c3d4e5f6g7h8i9j0'}
                },
                'required': ['session_id']
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Booking confirmed successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Booking confirmed successfully'},
                    'booking_id': {'type': 'integer'},
                    'seat_numbers': {'type': 'string'},
                    'total_amount': {'type': 'string'},
                    'status': {'type': 'string'},
                    'transaction_id': {'type': 'string'}
                }
            }
        },
        400: {'description': 'Bad request or payment not successful'},
        404: {'description': 'Booking not found'}
    }
})
def confirm_booking(current_user):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}
        result = confirm_booking_ctrl(current_user, data)
        return jsonify(result), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/user/bookings', methods=['GET'])
@token_required(msg='Please log in to view your bookings')
@swag_from({
    'tags': ['Booking'],
    'summary': 'Get user bookings',
    'description': 'Returns all bookings for the authenticated user',
    'security': [{'BearerAuth': []}],
    'responses': {
        200: {
            'description': 'User bookings retrieved successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Bookings retrieved successfully'},
                    'bookings': {'type': 'array'}
                }
            }
        }
    }
})
def get_user_bookings(current_user):
    try:
        result = get_user_bookings_ctrl(current_user)
        return jsonify(result), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/booking/<int:booking_id>/cancel', methods=['POST'])
@token_required(msg='Please log in to cancel a booking')
@swag_from({
    'tags': ['Booking'],
    'summary': 'Cancel booking',
    'description': 'Cancel a booking and initiate refund (if allowed by time rules)',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'booking_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1}
    ],
    'responses': {
        200: {
            'description': 'Booking cancelled and refunded successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'example': 'Booking cancelled and refunded successfully'},
                    'refund_amount': {'type': 'string'},
                    'refund_amount_value': {'type': 'number'}
                }
            }
        },
        400: {'description': 'Bad request or cancellation not allowed'},
        403: {'description': 'Unauthorized'},
        404: {'description': 'Booking not found'}
    }
})
def cancel_booking(current_user, booking_id):
    try:
        result = cancel_booking_ctrl(current_user, booking_id)
        return jsonify(result), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/booking/lock-seats', methods=['POST'])
@token_required(msg='Please log in to lock seats')
@swag_from({
    'tags': ['Booking'],
    'summary': 'Lock seats for booking (5-min timeout)',
    'description': 'Locks selected seats for 5 minutes for the current user. Prevents other users from booking these seats.',
    'security': [{'BearerAuth': []}],
    'parameters': [{
        'name': 'body',
        'in': 'body',
        'required': True,
        'schema': {
            'type': 'object',
            'properties': {
                'route_id': {'type': 'integer', 'example': 1},
                'seat_numbers': {'type': 'array', 'items': {'type': 'integer'}, 'example': [5, 6]}
            },
            'required': ['route_id', 'seat_numbers']
        }
    }],
    'responses': {
        200: {'description': 'Seats locked successfully'},
        400: {'description': 'Bad request'},
        409: {'description': 'Seat locked by another user'}
    }
})
def lock_seats(current_user):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}

        seat_ids = request.args.getlist('seat_id')
        if seat_ids:
            data['seat_numbers'] = [int(s) for s in seat_ids]

        result = lock_seats_ctrl(current_user, data)
        return jsonify(result), 200
    except BookingError as e:
        resp = {'message': str(e)}
        if e.extra:
            resp.update(e.extra)
        return jsonify(resp), e.status_code


@bp.route('/api/booking/release-seats', methods=['POST'])
@token_required(msg='Please log in to release seats')
@swag_from({
    'tags': ['Booking'],
    'summary': 'Release locked seats',
    'description': 'Releases previously locked seats for the current user.',
    'security': [{'BearerAuth': []}],
    'parameters': [{
        'name': 'body',
        'in': 'body',
        'required': True,
        'schema': {
            'type': 'object',
            'properties': {
                'route_id': {'type': 'integer', 'example': 1},
                'seat_numbers': {'type': 'array', 'items': {'type': 'integer'}, 'example': [5, 6]}
            },
            'required': ['route_id', 'seat_numbers']
        }
    }],
    'responses': {
        200: {'description': 'Seats released successfully'},
        400: {'description': 'Bad request'}
    }
})
def release_seats(current_user):
    try:
        body = request.get_json(silent=True) or {}
        data = {**dict(request.args), **body}

        seat_ids = request.args.getlist('seat_id')
        if seat_ids:
            data['seat_numbers'] = [int(s) for s in seat_ids]

        result = release_seats_ctrl(current_user, data)
        return jsonify(result), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/booking/<int:booking_id>/ticket', methods=['GET'])
@token_required(msg='Please log in to view your ticket')
@swag_from({
    'tags': ['Booking'],
    'summary': 'Get ticket data for a booking',
    'description': 'Returns the saved ticket blob for a completed booking.',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'booking_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1}
    ],
    'responses': {
        200: {'description': 'Ticket retrieved successfully'},
        404: {'description': 'Ticket not found'}
    }
})
def get_ticket(current_user, booking_id):
    try:
        result = get_ticket_data_ctrl(booking_id, current_user)
        return jsonify(result), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/booking/<int:booking_id>/ticket/preview', methods=['GET'])
@token_required(msg='Please log in to view your ticket')
def ticket_preview(current_user, booking_id):
    try:
        booking = Booking.query.get(booking_id)
        if not booking:
            return jsonify({'message': 'Booking not found'}), 404
        if booking.user_id != current_user.id and current_user.role not in ('admin', 'driver'):
            return jsonify({'message': 'Unauthorized'}), 403

        td = _build_ticket_data(booking)

        html = build_ticket_html(td)
        return html, 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return jsonify({'message': f'Failed to render ticket: {str(e)}'}), 500


@bp.route('/api/admin/bus-manifest/<int:route_id>', methods=['GET'])
@token_required(msg='Admin login required')
@admin_required
@swag_from({
    'tags': ['Admin'],
    'summary': 'Get bus manifest (all seats full)',
    'description': 'Returns detailed bus manifest with all passengers when all seats are booked.',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'route_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1}
    ],
    'responses': {
        200: {'description': 'Bus manifest retrieved'},
        400: {'description': 'Bus not yet full'}
    }
})
def admin_bus_manifest(current_user, route_id):
    try:
        result = get_bus_manifest_ctrl(route_id, current_user)
        return jsonify(result), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/driver/bus-manifest/<int:route_id>', methods=['GET'])
@token_required(msg='Driver login required')
@driver_required
@swag_from({
    'tags': ['Driver'],
    'summary': 'Get bus manifest for driver',
    'description': 'Returns detailed bus manifest with all passengers when all seats are booked.',
    'security': [{'BearerAuth': []}],
    'parameters': [
        {'name': 'route_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1}
    ],
    'responses': {
        200: {'description': 'Bus manifest retrieved'},
        400: {'description': 'Bus not yet full'}
    }
})
def driver_bus_manifest(current_user, route_id):
    try:
        result = get_bus_manifest_ctrl(route_id, current_user)
        return jsonify(result), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/booking/webhook', methods=['POST'])
@swag_from({
    'tags': ['Booking'],
    'summary': 'Stripe webhook',
    'description': 'Handle Stripe webhook events',
    'responses': {
        200: {
            'description': 'Webhook processed successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'}
                }
            }
        },
        400: {'description': 'Invalid payload or signature'}
    }
})
def stripe_webhook():
    try:
        payload = request.get_data(as_text=True)
        sig_header = request.headers.get('Stripe-Signature')
        handle_stripe_webhook(payload, sig_header)
        return jsonify({'status': 'success'}), 200
    except BookingError as e:
        return jsonify({'message': str(e)}), e.status_code


@bp.route('/api/ticket/pdf/<base64_id>', methods=['GET'])
@swag_from({
    'tags': ['Ticket'],
    'summary': 'Download ticket PDF by base64 ID',
    'description': 'Returns the stored PDF for a ticket using its unique base64 identifier. No auth required (designed for QR code scanning).',
    'parameters': [
        {'name': 'base64_id', 'in': 'path', 'type': 'string', 'required': True, 'example': 'abc123...'}
    ],
    'responses': {
        200: {'description': 'PDF file returned'},
        404: {'description': 'Ticket not found'},
        500: {'description': 'Server error'}
    }
})
def download_ticket_pdf_by_base64_id(base64_id):
    try:
        doc = TicketDocument.query.filter_by(base64_id=base64_id).first()
        if not doc:
            return jsonify({'message': 'Ticket not found'}), 404
        response = make_response(doc.pdf_blob)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=ticket_{doc.booking_id}.pdf'
        return response
    except Exception as e:
        return jsonify({'message': f'Failed to retrieve PDF: {str(e)}'}), 500


@bp.route('/api/booking/<int:booking_id>/ticket/pdf', methods=['GET'])
@swag_from({
    'tags': ['Booking'],
    'summary': 'Download ticket as PDF',
    'description': 'Generates and downloads a PDF copy of the ticket.',
    'parameters': [
        {'name': 'booking_id', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1}
    ],
    'responses': {
        200: {'description': 'PDF file downloaded'},
        404: {'description': 'Booking not found'},
        500: {'description': 'PDF generation failed'}
    }
})
def download_ticket_pdf(booking_id):
    try:
        booking = Booking.query.get(booking_id)
        if not booking:
            return jsonify({'message': 'Booking not found'}), 404

        ticket = Ticket.query.filter_by(booking_id=booking.id).first()
        if ticket:
            td = json.loads(ticket.ticket_data)
        else:
            td = _build_ticket_data(booking)

        pdf_url = f'{request.host_url}api/booking/{booking_id}/ticket/pdf'
        html = build_premium_ticket_html(td)

        buf = io.BytesIO()
        result = pisa.CreatePDF(html, dest=buf)
        if result.err:
            return jsonify({'message': 'PDF generation failed'}), 500

        pdf_bytes = buf.getvalue()
        filename = f'ticket_{booking.reference_id or booking.id}.pdf'
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        return response

    except BookingError:
        raise
    except Exception as e:
        return jsonify({'message': f'Failed to generate PDF: {str(e)}'}), 500
