import secrets
import string
from datetime import date, datetime

import stripe
from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..core.auth import format_indian_phone, validate_indian_phone
from ..core.extensions import db, stripe as stripe_module
from ..core.models import Booking, PassengerDetail, PaymentLog, Route, SeatStatus, User

MIN_BULK_SEATS = 5


class AgencyError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _calculate_age(birth_date):
    if not birth_date:
        return None
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def create_bulk_booking(current_user, data):
    try:
        route_id = data.get('route_id')
        seat_numbers = data.get('seat_numbers')

        if not route_id:
            raise AgencyError('route_id is required', 400)
        if not seat_numbers:
            raise AgencyError('seat_numbers is required', 400)

        if not isinstance(seat_numbers, list):
            seat_numbers = [int(s.strip()) for s in str(seat_numbers).split(',')]

        if len(seat_numbers) < MIN_BULK_SEATS:
            raise AgencyError(f'Must book at least {MIN_BULK_SEATS} seats per order', 400)

        route = Route.query.get(route_id)
        if not route:
            raise AgencyError('Route not found', 404)

        passengers = data.get('passengers')
        if not passengers or not isinstance(passengers, list):
            raise AgencyError('passengers array is required with details for each seat', 400)
        if len(passengers) != len(seat_numbers):
            raise AgencyError(f'Provide details for all {len(seat_numbers)} seats ({len(passengers)} given)', 400)

        provided_nums = [str(p.get('seat_number', '')) for p in passengers]
        expected_nums = [str(s) for s in seat_numbers]
        if set(provided_nums) != set(expected_nums):
            raise AgencyError(f'Passenger seat_numbers must match: {", ".join(expected_nums)}', 400)

        total_amount = 0
        for seat_num in seat_numbers:
            seat = SeatStatus.query.filter_by(route_id=route.id, seat_number=seat_num).first()
            if not seat:
                raise AgencyError(f'Seat {seat_num} not found', 400)
            if seat.is_booked:
                raise AgencyError(f'Seat {seat_num} is already booked', 400)
            total_amount += route.price * seat.price_multiplier

        alphabet = string.ascii_uppercase + string.digits
        while True:
            ref_id = ''.join(secrets.choice(alphabet) for _ in range(12))
            if not Booking.query.filter_by(reference_id=ref_id).first():
                break

        booking = Booking(
            user_id=current_user.id,
            route_id=route.id,
            seat_numbers=','.join(map(str, seat_numbers)),
            total_amount=total_amount,
            status='booked',
            payment_status='pending',
            reference_id=ref_id,
            passenger_name=passengers[0].get('name', current_user.name),
            passenger_email=data.get('passenger_email', current_user.email),
            passenger_phone=format_indian_phone(data.get('passenger_phone', current_user.phone)) if data.get('passenger_phone', current_user.phone) else None,
            special_requests=data.get('special_requests'),
        )
        db.session.add(booking)
        db.session.flush()

        for p in passengers:
            seat_num = p.get('seat_number')
            if not seat_num:
                raise AgencyError('seat_number is required for each passenger', 400)
            if not p.get('name'):
                raise AgencyError(f'name is required for seat {seat_num}', 400)
            if not p.get('gender'):
                raise AgencyError(f'gender is required for seat {seat_num}', 400)
            if not p.get('phone'):
                raise AgencyError(f'phone is required for seat {seat_num}', 400)
            if not p.get('boarding_location'):
                raise AgencyError(f'boarding_location is required for seat {seat_num}', 400)

            p_age = p.get('age')
            p_birth_date = p.get('birth_date')
            calculated_age = None
            parsed_birth_date = None
            if p_birth_date:
                parsed_birth_date = datetime.strptime(str(p_birth_date), '%Y-%m-%d').date()
                calculated_age = _calculate_age(parsed_birth_date)
            elif p_age:
                calculated_age = int(p_age)
            else:
                raise AgencyError(f'age or birth_date is required for seat {seat_num}', 400)

            seat = SeatStatus.query.filter_by(route_id=route.id, seat_number=seat_num).first()
            if seat:
                seat.booking_id = booking.id

            detail = PassengerDetail(
                booking_id=booking.id,
                seat_number=seat_num,
                name=p['name'],
                age=calculated_age,
                gender=p['gender'],
                phone=format_indian_phone(p['phone']) if p.get('phone') else None,
                email=p.get('email'),
                birth_date=parsed_birth_date,
                boarding_location=p['boarding_location'],
                medications=p.get('medications'),
            )
            db.session.add(detail)

        session = stripe_module.checkout.Session.create(
            mode='payment',
            payment_method_types=['card', 'upi'],
            customer_email=current_user.email,
            line_items=[{
                'price_data': {
                    'currency': 'inr',
                    'product_data': {
                        'name': f'Bulk Booking: {route.source} to {route.destination}',
                        'description': f'Agency: {current_user.name} | Seats: {", ".join(map(str, seat_numbers))}',
                    },
                    'unit_amount': int(total_amount * 100),
                },
                'quantity': 1,
            }],
            metadata={
                'booking_id': str(booking.id),
                'user_id': str(current_user.id),
                'route_id': str(route.id),
                'seat_numbers': ','.join(map(str, seat_numbers)),
                'source': route.source,
                'destination': route.destination,
                'booking_type': 'bulk',
                'agency_name': current_user.name,
            },
            success_url=current_app.config['PAYMENT_SUCCESS_URL'] + '?session_id={CHECKOUT_SESSION_ID}&booking_id=' + str(booking.id),
            cancel_url=current_app.config['PAYMENT_CANCEL_URL'] + '?booking_id=' + str(booking.id),
        )

        booking.checkout_session_id = session.id

        payment_log = PaymentLog(
            checkout_session_id=session.id,
            booking_id=booking.id,
            amount=total_amount,
            currency='inr',
            status='initiated',
            description=f'Bulk booking #{booking.id} by agency {current_user.name}: {route.source} to {route.destination}',
            bus_number=route.bus.bus_number,
            bus_type=route.bus.bus_type,
            response_data=str(session),
        )
        db.session.add(payment_log)
        db.session.commit()

        return {
            'message': 'Bulk booking initiated successfully',
            'booking_id': booking.id,
            'checkout_url': session.url,
            'checkout_session_id': session.id,
            'total_seats': len(seat_numbers),
            'amount': f"INR {total_amount:.2f}",
            'amount_value': total_amount,
        }

    except stripe.error.StripeError as e:
        db.session.rollback()
        raise AgencyError(f'Payment service error: {str(e)}', 400) from e
    except IntegrityError as e:
        db.session.rollback()
        raise AgencyError('Database integrity error during booking', 500) from e
    except AgencyError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AgencyError(f'Bulk booking failed: {str(e)}', 500) from e


def get_agency_bookings(current_user):
    try:
        bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.booking_date.desc()).all()
        result = []
        for booking in bookings:
            route = Route.query.get(booking.route_id)
            payment_log = PaymentLog.query.filter_by(booking_id=booking.id).order_by(PaymentLog.created_at.desc()).first()
            result.append({
                'booking_id': booking.id,
                'route': {
                    'source': route.source if route else None,
                    'destination': route.destination if route else None,
                    'departure_time': route.departure_time.isoformat() if route else None,
                    'arrival_time': route.arrival_time.isoformat() if route else None,
                },
                'seat_numbers': booking.seat_numbers,
                'total_seats': len(booking.seat_numbers.split(',')) if booking.seat_numbers else 0,
                'total_amount': f"INR {booking.total_amount:.2f}",
                'total_amount_value': booking.total_amount,
                'booking_date': booking.booking_date.isoformat(),
                'status': booking.status,
                'payment_status': booking.payment_status,
                'payment_description': payment_log.description if payment_log else None,
            })
        return {'message': 'Bookings retrieved successfully', 'bookings': result}

    except Exception as e:
        raise AgencyError(f'Failed to fetch bookings: {str(e)}', 500) from e
