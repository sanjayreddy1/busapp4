import base64
import io
import json
import secrets
import string
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote
from threading import Lock

import qrcode  # type: ignore[import-untyped]
import stripe
from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..core.auth import format_indian_phone, validate_indian_phone
from ..core.extensions import db, stripe as stripe_module
from ..core.models import (
    Booking,
    CompletedPayment,
    PassengerDetail,
    PaymentLog,
    Route,
    SeatLock,
    SeatStatus,
    Ticket,
    TicketDocument,
    User,
)

LOCK_DURATION_MINUTES = 5
BOOKING_TIMEOUT_MINUTES = 5


class BookingError(Exception):
    def __init__(self, message, status_code=400, extra=None):
        super().__init__(message)
        self.status_code = status_code
        self.extra = extra or {}


def _calculate_age(birth_date):
    if not birth_date:
        return None
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


_REF_ID_ALPHABET = string.ascii_uppercase + string.digits


def _generate_reference_id():
    return ''.join(secrets.choice(_REF_ID_ALPHABET) for _ in range(8))


def release_expired_locks():
    now = datetime.utcnow()
    expired = SeatLock.query.filter(SeatLock.expires_at <= now).all()
    for lock in expired:
        db.session.delete(lock)
    if expired:
        db.session.commit()
    return len(expired)


def lock_seats(current_user, data):
    try:
        route_id = data.get('route_id')
        seat_numbers = data.get('seat_numbers')

        if not route_id:
            raise BookingError('route_id is required', 400)
        if not seat_numbers:
            raise BookingError('seat_numbers is required', 400)

        if not isinstance(seat_numbers, list):
            seat_numbers = [int(s.strip()) for s in str(seat_numbers).split(',')]

        if current_user.role == 'user' and len(seat_numbers) > 4:
            raise BookingError('Users can only book up to 4 seats', 400)

        route = Route.query.get(route_id)
        if not route:
            raise BookingError('Route not found', 404)

        release_expired_locks()

        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=LOCK_DURATION_MINUTES)

        seats = SeatStatus.query.filter(
            SeatStatus.route_id == route.id,
            SeatStatus.seat_number.in_(seat_numbers),
        ).all()
        seats_by_number = {s.seat_number: s for s in seats}
        for sn in seat_numbers:
            if sn not in seats_by_number:
                all_seats = SeatStatus.query.filter(
                    SeatStatus.route_id == route.id
                ).order_by(SeatStatus.seat_number).all()
                available = [s.seat_number for s in all_seats if not s.is_booked]
                raise BookingError(
                    f'Seat {sn} not found',
                    400,
                    extra={'available_seats': available, 'total_seats': route.bus.total_seats},
                )
            seat = seats_by_number[sn]
            if seat.is_booked:
                raise BookingError(f'Seat {sn} is already booked', 400)

        booking_ids = {s.booking_id for s in seats if s.booking_id is not None}
        bookings_by_id = {}
        if booking_ids:
            for b in Booking.query.filter(Booking.id.in_(booking_ids)).all():
                bookings_by_id[b.id] = b

        for seat in seats:
            if seat.booking_id is not None:
                existing_booking = bookings_by_id.get(seat.booking_id)
                if existing_booking and existing_booking.payment_status == 'pending':
                    raise BookingError(f'Seat {seat.seat_number} is already being booked', 409)
                if existing_booking and existing_booking.payment_status in ('paid', 'succeeded'):
                    raise BookingError(f'Seat {seat.seat_number} is already booked and paid', 400)

        locks = SeatLock.query.filter(
            SeatLock.route_id == route.id,
            SeatLock.seat_number.in_(seat_numbers),
        ).all()
        locks_by_number = {l.seat_number: l for l in locks}

        locked_seats = []
        for sn in seat_numbers:
            existing_lock = locks_by_number.get(sn)
            if existing_lock:
                if existing_lock.user_id != current_user.id:
                    raise BookingError(f'Seat {sn} is locked by another user', 409)
                existing_lock.expires_at = expires_at
                existing_lock.locked_at = now
            else:
                db.session.add(SeatLock(
                    route_id=route.id, seat_number=sn,
                    user_id=current_user.id, locked_at=now, expires_at=expires_at,
                ))
            locked_seats.append({'seat_number': sn, 'locked_until': expires_at.isoformat()})

        db.session.commit()

        return {
            'message': 'Seats locked successfully',
            'route_id': route.id,
            'seat_numbers': [s['seat_number'] for s in locked_seats],
            'locked_until': expires_at.isoformat(),
            'expires_in_minutes': LOCK_DURATION_MINUTES,
        }

    except BookingError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise BookingError(f'Seat locking failed: {str(e)}', 500) from e


def release_seats(current_user, data):
    try:
        route_id = data.get('route_id')
        seat_numbers = data.get('seat_numbers')

        if not route_id:
            raise BookingError('route_id is required', 400)
        if not seat_numbers:
            raise BookingError('seat_numbers is required', 400)

        if not isinstance(seat_numbers, list):
            seat_numbers = [int(s.strip()) for s in str(seat_numbers).split(',')]

        locks = SeatLock.query.filter(
            SeatLock.route_id == route_id,
            SeatLock.seat_number.in_(seat_numbers),
            SeatLock.user_id == current_user.id,
        ).all()

        removed = []
        for lock in locks:
            db.session.delete(lock)
            removed.append(lock.seat_number)

        db.session.commit()

        return {
            'message': 'Seats released successfully',
            'released_seats': removed,
        }

    except BookingError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise BookingError(f'Seat release failed: {str(e)}', 500) from e


_initiate_booking_lock = Lock()


def initiate_booking(current_user, data):
    """Create a booking + Stripe checkout session."""

    # ── Validation (outside retry loop) ──
    if 'route_id' not in data:
        raise BookingError('route_id is required', 400)
    if 'seat_numbers' not in data:
        raise BookingError('seat_numbers is required', 400)

    route = Route.query.get(data['route_id'])
    if not route:
        raise BookingError('Route not found in the database', 404)

    seat_numbers = data['seat_numbers']
    if seat_numbers is None:
        raise BookingError('seat_numbers is required', 400)
    if not isinstance(seat_numbers, list):
        seat_numbers = [int(s.strip()) for s in str(seat_numbers).split(',')]

    try:
        seat_numbers = [int(s) for s in seat_numbers]
    except Exception:
        raise BookingError('seat_numbers must contain integers', 400)

    if len(seat_numbers) == 0:
        raise BookingError('At least one seat must be selected', 400)
    if any(s <= 0 for s in seat_numbers):
        raise BookingError('Invalid seat_numbers. Seat number must be positive', 400)
    if len(set(seat_numbers)) != len(seat_numbers):
        raise BookingError('Duplicate seat numbers are not allowed', 400)

    passenger_phone = data.get('passenger_phone', current_user.phone)
    if passenger_phone and not validate_indian_phone(passenger_phone):
        raise BookingError('Invalid Indian phone number for passenger', 400)

    ref_id = _generate_reference_id()
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            release_expired_locks()

            seats = SeatStatus.query.filter(
                SeatStatus.route_id == route.id,
                SeatStatus.seat_number.in_(seat_numbers),
            ).all()
            if len(seats) != len(seat_numbers):
                found = {s.seat_number for s in seats}
                missing = [s for s in seat_numbers if s not in found]
                all_seats = SeatStatus.query.filter(
                    SeatStatus.route_id == route.id
                ).order_by(SeatStatus.seat_number).all()
                available = [
                    s.seat_number for s in all_seats
                    if not s.is_booked
                ]
                raise BookingError(
                    f'Seat(s) not found: {missing}',
                    400,
                    extra={'available_seats': available, 'total_seats': route.bus.total_seats},
                )

            locks = SeatLock.query.filter(
                SeatLock.route_id == route.id,
                SeatLock.seat_number.in_(seat_numbers),
            ).all()
            locked_by_map = {l.seat_number: l.user_id for l in locks}

            booked_seats = [s for s in seats if s.is_booked]
            booked_by_ids = {s.booked_by for s in booked_seats if s.booked_by}
            users_by_id = {}
            if booked_by_ids:
                for u in User.query.filter(User.id.in_(booked_by_ids)).all():
                    users_by_id[u.id] = u

            booking_ids = {s.booking_id for s in seats if s.booking_id is not None}
            bookings_by_id = {}
            if booking_ids:
                for b in Booking.query.filter(Booking.id.in_(booking_ids)).all():
                    bookings_by_id[b.id] = b

            conflicts = []
            total_amount = 0
            for seat in seats:
                if seat.is_booked:
                    if seat.booked_by:
                        booker = users_by_id.get(seat.booked_by)
                        if booker and booker.gender != current_user.gender:
                            if {booker.gender, current_user.gender} == {'male', 'female'}:
                                conflicts.append({'seat': seat.seat_number, 'reason': 'Booked by opposite gender passenger'})
                                continue
                    conflicts.append({'seat': seat.seat_number, 'reason': 'Already booked by another passenger'})
                    continue

                locked_by_user_id = locked_by_map.get(seat.seat_number)
                if locked_by_user_id is not None and locked_by_user_id != current_user.id:
                    conflicts.append({'seat': seat.seat_number, 'reason': 'Temporarily locked by another user'})
                    continue

                if seat.booking_id is not None:
                    existing = bookings_by_id.get(seat.booking_id)
                    if existing and existing.payment_status == 'pending':
                        conflicts.append({'seat': seat.seat_number, 'reason': 'Currently being booked by another user'})
                        continue
                    if existing and existing.payment_status in ('paid', 'succeeded'):
                        conflicts.append({'seat': seat.seat_number, 'reason': 'Already booked and paid'})
                        continue

                total_amount += route.price * seat.price_multiplier

            if conflicts:
                free_seats = SeatStatus.query.filter(
                    SeatStatus.route_id == route.id,
                    SeatStatus.is_booked == False,
                    SeatStatus.booking_id == None,
                ).all()
                locked_nums = {
                    l.seat_number
                    for l in SeatLock.query.filter(
                        SeatLock.route_id == route.id,
                        SeatLock.expires_at > datetime.utcnow(),
                    ).all()
                }
                free_list = [
                    {'seat_number': s.seat_number, 'seat_type': s.seat_type}
                    for s in free_seats
                    if s.seat_number not in locked_nums
                ]
                raise BookingError(
                    'The selected seat was booked already you can select some other seat',
                    409,
                    extra={'conflicts': conflicts, 'available_seats': free_list},
                )

            passenger_name = data.get('passenger_name')
            if not passenger_name:
                passengers_data = data.get('passengers', [])
                if passengers_data:
                    passenger_name = passengers_data[0].get('name')
                if not passenger_name:
                    passenger_name = current_user.name

            booking = Booking(
                user_id=current_user.id,
                route_id=route.id,
                seat_numbers=','.join(map(str, seat_numbers)),
                total_amount=total_amount,
                status='booked',
                payment_status='pending',
                reference_id=ref_id,
                passenger_name=passenger_name,
                passenger_email=data.get('passenger_email', current_user.email),
                passenger_phone=format_indian_phone(passenger_phone) if passenger_phone else None,
                special_requests=data.get('special_requests'),
            )
            db.session.add(booking)
            db.session.flush()

            seats_by_number = {s.seat_number: s for s in seats}
            for sn in seat_numbers:
                seat = seats_by_number.get(sn)
                if seat:
                    seat.booking_id = booking.id

            passengers_data = data.get('passengers', [])
            if passengers_data:
                for p in passengers_data:
                    p_age = p.get('age')
                    p_birth_date = p.get('birth_date')
                    calculated_age = None
                    if p_birth_date:
                        calculated_age = _calculate_age(datetime.strptime(str(p_birth_date), '%Y-%m-%d').date())
                    elif p_age:
                        calculated_age = int(p_age)

                    db.session.add(PassengerDetail(
                        booking_id=booking.id,
                        seat_number=p.get('seat_number', seat_numbers[0]),
                        name=p.get('name'),
                        age=calculated_age,
                        gender=p.get('gender'),
                        phone=p.get('phone'),
                        email=p.get('email'),
                        birth_date=datetime.strptime(str(p_birth_date), '%Y-%m-%d').date() if p_birth_date else None,
                        boarding_location=p.get('boarding_location'),
                        medications=p.get('medications'),
                    ))
            else:
                for sn in seat_numbers:
                    db.session.add(PassengerDetail(
                        booking_id=booking.id,
                        seat_number=sn,
                        name=data.get('passenger_name'),
                        age=data.get('passenger_age'),
                        gender=data.get('passenger_gender'),
                        phone=format_indian_phone(passenger_phone) if passenger_phone else None,
                        email=data.get('passenger_email', current_user.email),
                    ))

            locks_to_delete = SeatLock.query.filter(
                SeatLock.route_id == route.id,
                SeatLock.seat_number.in_(seat_numbers),
                SeatLock.user_id == current_user.id,
            ).all()
            for lock in locks_to_delete:
                db.session.delete(lock)

            # Create Stripe session after flush to reduce SQLite lock window.
            session = stripe_module.checkout.Session.create(
                mode='payment',
                payment_method_types=['card', 'upi'],
                payment_method_options={'card': {'installments': {'enabled': True}}},
                customer_email=current_user.email,
                line_items=[{
                    'price_data': {
                        'currency': 'inr',
                        'product_data': {
                            'name': f'Bus Booking: {route.source} to {route.destination}',
                            'description': f'Seats: {", ".join(map(str, seat_numbers))} | {route.bus.bus_number}',
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
                },
                success_url=(
                    current_app.config['PAYMENT_SUCCESS_URL']
                    + '?session_id={CHECKOUT_SESSION_ID}&booking_id='
                    + str(booking.id)
                ),
                cancel_url=(
                    current_app.config['PAYMENT_CANCEL_URL']
                    + '?booking_id='
                    + str(booking.id)
                ),
            )

            booking.checkout_session_id = session.id
            db.session.add(PaymentLog(
                checkout_session_id=session.id,
                booking_id=booking.id,
                amount=total_amount,
                currency='inr',
                status='initiated',
                description=(f'Payment initiated for booking #{booking.id}: {route.source} to {route.destination}'),
                bus_number=route.bus.bus_number,
                bus_type=route.bus.bus_type,
                response_data=str(session),
            ))
            db.session.commit()

            return {
                'message': 'Booking initiated successfully',
                'booking_id': booking.id,
                'checkout_url': session.url,
                'checkout_session_id': session.id,
                'amount': f"INR {total_amount:.2f}",
                'amount_value': total_amount,
            }

        except stripe.error.StripeError as e:
            db.session.rollback()
            raise BookingError(f'Payment service error: {str(e)}', 400) from e
        except IntegrityError as e:
            db.session.rollback()
            raise BookingError('Database integrity error during booking', 500) from e
        except BookingError:
            db.session.rollback()
            raise
        except Exception as e:
            db.session.rollback()
            msg = str(e).lower()
            if ('database is locked' in msg or 'database is busy' in msg) and attempt < max_attempts:
                time.sleep(0.2 * attempt)
                continue
            raise BookingError(f'Booking initiation failed: {str(e)}', 500) from e

    raise BookingError('Booking initiation failed after retries', 500)



def confirm_booking(current_user, data):
    try:
        session_id = data.get('session_id')
        if not session_id:
            raise BookingError('Checkout session ID is required', 400)

        session = stripe_module.checkout.Session.retrieve(session_id)

        if session.payment_status != 'paid':
            raise BookingError('Payment not successful', 400)

        booking = Booking.query.filter_by(
            id=session.metadata.get('booking_id'),
            user_id=current_user.id,
        ).with_for_update().first()

        if not booking:
            raise BookingError('Booking not found in the database', 404)

        if booking.payment_status in ('paid', 'succeeded'):
            ticket = Ticket.query.filter_by(booking_id=booking.id).first()
            ticket_data = json.loads(ticket.ticket_data) if ticket else None
            return {
                'message': 'Booking already confirmed',
                'booking_id': booking.id,
                'seat_numbers': booking.seat_numbers,
                'total_amount': f"INR {booking.total_amount:.2f}",
                'total_amount_value': booking.total_amount,
                'status': booking.status,
                'transaction_id': booking.transaction_id,
                'payment': {
                    'transaction_id': booking.transaction_id,
                    'amount': booking.total_amount,
                    'formatted_amount': f"₹{booking.total_amount:,.0f}",
                    'currency': 'INR',
                    'payment_status': booking.payment_status,
                    'mode': (ticket_data or {}).get('mode_of_payment'),
                    'bank_details': (ticket_data or {}).get('customer_bank_details'),
                },
                'ticket': ticket_data,
            }

        seat_numbers = [int(s) for s in booking.seat_numbers.split(',')]

        # Check if booking session expired (user was inactive too long)
        if booking.payment_status == 'pending':
            elapsed = (datetime.utcnow() - booking.booking_date).total_seconds()
            if elapsed > BOOKING_TIMEOUT_MINUTES * 60:
                booking.status = 'cancelled'
                booking.payment_status = 'failed'
                seats = SeatStatus.query.filter(
                    SeatStatus.route_id == booking.route_id,
                    SeatStatus.seat_number.in_(seat_numbers),
                    SeatStatus.booking_id == booking.id,
                ).all()
                for seat in seats:
                    seat.booking_id = None
                db.session.commit()
                raise BookingError(
                    f'Booking session expired after {BOOKING_TIMEOUT_MINUTES} minutes of inactivity. '
                    'Please start a new booking.',
                    400,
                )

        # Final seat integrity check to prevent double-booking
        seats = SeatStatus.query.filter(
            SeatStatus.route_id == booking.route_id,
            SeatStatus.seat_number.in_(seat_numbers),
        ).all()
        if len(seats) != len(seat_numbers):
            raise BookingError('Seat configuration mismatch for this booking', 409)

        # If any seat is already booked by someone else => fail
        for seat in seats:
            if seat.is_booked and seat.booked_by != current_user.id:
                raise BookingError(f'Seat {seat.seat_number} is already booked', 409)

        for seat in seats:
            seat.is_booked = True
            seat.booked_by = current_user.id
            seat.booking_id = booking.id


        route = booking.route
        if route:
            route.available_seats -= len(seat_numbers)

        booking.status = 'booked'
        booking.payment_status = 'succeeded'
        booking.payment_intent_id = session.payment_intent
        booking.transaction_id = session.id

        try:
            pi = stripe_module.PaymentIntent.retrieve(session.payment_intent)
            payment_details = _extract_payment_details(pi)
        except Exception:
            payment_details = None

        payment_log = PaymentLog.query.filter_by(checkout_session_id=session_id).first()
        if payment_log:
            payment_log.status = 'completed'
            payment_log.payment_intent_id = session.payment_intent
            payment_log.description = f'Payment successful for booking #{booking.id}'
            if payment_details:
                payment_log.payment_method = payment_details.get('mode_of_payment')
            if route and route.bus:
                payment_log.bus_number = route.bus.bus_number
                payment_log.bus_type = route.bus.bus_type

        ticket_data = None
        try:
            ticket_data = _save_ticket_blob(booking, payment_details)
        except Exception as e:
            current_app.logger.error(f"confirm_booking: failed to save ticket blob for booking {booking.id}: {e}")
        try:
            _save_completed_payment(booking, payment_details, ticket_data)
        except Exception as e:
            current_app.logger.error(f"confirm_booking: failed to save completed_payment for booking {booking.id}: {e}")
        try:
            from flask import request as _req
            host = _req.host_url if _req else None
            _save_ticket_document(booking, ticket_data, host_url=host)
        except Exception as e:
            current_app.logger.error(f"confirm_booking: failed to save ticket document for booking {booking.id}: {e}")
        _release_expired_locks_for_booking(booking)

        db.session.commit()

        return {
            'message': 'Booking confirmed successfully',
            'booking_id': booking.id,
            'seat_numbers': booking.seat_numbers,
            'total_amount': f"INR {booking.total_amount:.2f}",
            'total_amount_value': booking.total_amount,
            'status': booking.status,
            'transaction_id': booking.transaction_id,
            'payment': {
                'transaction_id': booking.transaction_id,
                'amount': booking.total_amount,
                'formatted_amount': f"₹{booking.total_amount:,.0f}",
                'currency': 'INR',
                'payment_status': booking.payment_status,
                'mode': (payment_details or {}).get('mode_of_payment'),
                'bank_details': (payment_details or {}).get('bank_details'),
            },
            'ticket': ticket_data,
        }

    except stripe.error.StripeError as e:
        raise BookingError(f'Payment verification failed: {str(e)}', 400) from e
    except BookingError:
        raise
    except Exception as e:
        db.session.rollback()
        raise BookingError(f'Booking confirmation failed: {str(e)}', 500) from e


def get_user_bookings(current_user):
    try:
        bookings = Booking.query.filter_by(user_id=current_user.id).all()

        result = []
        for booking in bookings:
            route = Route.query.get(booking.route_id)
            payment_log = (
                PaymentLog.query.filter_by(booking_id=booking.id)
                .order_by(PaymentLog.created_at.desc())
                .first()
            )
            result.append({
                'booking_id': booking.id,
                'route': {
                    'source': route.source,
                    'destination': route.destination,
                    'departure_time': route.departure_time.isoformat(),
                    'arrival_time': route.arrival_time.isoformat(),
                },
                'seat_numbers': booking.seat_numbers,
                'total_amount': f"INR {booking.total_amount:.2f}",
                'total_amount_value': booking.total_amount,
                'booking_date': booking.booking_date.isoformat(),
                'status': booking.status,
                'payment_status': booking.payment_status,
                'payment_description': payment_log.description if payment_log else None,
                'transaction_id': booking.transaction_id,
                'passenger_name': booking.passenger_name,
                'passenger_phone': booking.passenger_phone,
                'special_requests': booking.special_requests,
            })

        return {'message': 'Bookings retrieved successfully', 'bookings': result}

    except Exception as e:
        raise BookingError(f'Failed to fetch bookings: {str(e)}', 500) from e


def cancel_booking(current_user, booking_id):
    try:
        if booking_id is None:
            raise BookingError('Booking ID is required', 400)

        booking = Booking.query.get(booking_id)
        if not booking:
            raise BookingError('Booking not found in the database', 404)

        if not current_user:
            raise BookingError('Authentication required', 401)

        if booking.user_id != current_user.id:
            raise BookingError('Unauthorized', 403)

        if booking.status == 'cancelled':
            raise BookingError('Booking already cancelled', 400)

        route = Route.query.get(booking.route_id)
        current_time = datetime.utcnow()
        time_until_departure = (route.departure_time - current_time).total_seconds() / 3600

        if time_until_departure < 2:
            raise BookingError('Cannot cancel within 2 hours of departure', 400)

        seat_numbers = [int(s) for s in booking.seat_numbers.split(',')]
        seats = SeatStatus.query.filter(
            SeatStatus.route_id == booking.route_id,
            SeatStatus.seat_number.in_(seat_numbers),
        ).all()
        for seat in seats:
            seat.is_booked = False
            seat.booked_by = None
            seat.booking_id = None

        route.available_seats += len(seat_numbers)
        booking.status = 'cancelled'
        booking.payment_status = 'refunded'

        try:
            stripe_module.Refund.create(
                payment_intent=booking.payment_intent_id,
                amount=int(booking.total_amount * 100),
            )
        except stripe.error.StripeError as e:
            raise BookingError(f'Refund failed: {str(e)}', 400) from e

        payment_log = (
            PaymentLog.query.filter_by(booking_id=booking.id)
            .order_by(PaymentLog.created_at.desc())
            .first()
        )
        if payment_log:
            payment_log.status = 'fail'
            payment_log.description = f'Payment refunded for cancelled booking #{booking.id}'

        db.session.commit()

        return {
            'message': 'Booking cancelled and refunded successfully',
            'refund_amount': f"INR {booking.total_amount:.2f}",
            'refund_amount_value': booking.total_amount,
        }

    except BookingError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise BookingError(f'Cancellation failed: {str(e)}', 500) from e


def update_booking(current_user, booking_id, data):
    try:
        if booking_id is None:
            raise BookingError('Booking ID is required', 400)

        booking = Booking.query.get(booking_id)
        if not booking:
            raise BookingError('Booking not found in the database', 404)

        if not current_user:
            raise BookingError('Authentication required', 401)

        if booking.user_id != current_user.id and current_user.role != 'admin':
            raise BookingError('Unauthorized', 403)

        if booking.status == 'booked':
            raise BookingError('Cannot update confirmed booking', 400)

        if 'passenger_name' in data:
            booking.passenger_name = data['passenger_name']

        if 'passenger_email' in data:
            booking.passenger_email = data['passenger_email']

        if 'special_requests' in data:
            booking.special_requests = data['special_requests']

        if 'passenger_phone' in data:
            if data['passenger_phone']:
                if not validate_indian_phone(data['passenger_phone']):
                    raise BookingError('Invalid Indian phone number', 400)
                booking.passenger_phone = format_indian_phone(data['passenger_phone'])

        booking.updated_at = datetime.utcnow()
        db.session.commit()

        return {
            'id': booking.id,
            'passenger_name': booking.passenger_name,
            'passenger_email': booking.passenger_email,
            'passenger_phone': booking.passenger_phone,
            'special_requests': booking.special_requests,
            'updated_at': booking.updated_at.isoformat(),
        }

    except BookingError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise BookingError(f'Booking update failed: {str(e)}', 500) from e


def handle_stripe_webhook(payload, sig_header):
    webhook_secret = current_app.config.get('STRIPE_WEBHOOK_SECRET', '')

    try:
        event = stripe_module.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError as e:
        raise BookingError('Invalid payload', 400) from e
    except stripe.error.SignatureVerificationError as e:
        raise BookingError('Invalid signature', 400) from e

    try:
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            booking_id = session.get('metadata', {}).get('booking_id')
            if not booking_id:
                return

            booking = Booking.query.filter_by(id=int(booking_id)).with_for_update().first()
            if not booking or booking.status != 'booked':
                return
            if booking.payment_status in ('paid', 'succeeded'):
                return

            seat_numbers = [int(s) for s in booking.seat_numbers.split(',')]

            # Seat integrity check (prevents double booking)
            seats = SeatStatus.query.filter(
                SeatStatus.route_id == booking.route_id,
                SeatStatus.seat_number.in_(seat_numbers),
            ).all()
            if len(seats) != len(seat_numbers):
                raise BookingError('Seat configuration mismatch for this booking', 409)

            for seat in seats:
                if seat.is_booked and seat.booked_by != booking.user_id:
                    raise BookingError(f'Seat {seat.seat_number} is already booked', 409)

            for seat in seats:
                seat.is_booked = True
                seat.booked_by = booking.user_id
                seat.booking_id = booking.id


            route = booking.route
            if route:
                route.available_seats -= len(seat_numbers)

            booking.status = 'booked'
            booking.payment_status = 'paid'
            booking.payment_intent_id = session.get('payment_intent')
            booking.transaction_id = session.get('id')

            try:
                pi = stripe_module.PaymentIntent.retrieve(session.get('payment_intent'))
                payment_details = _extract_payment_details(pi)
            except Exception:
                payment_details = None

            payment_log = PaymentLog.query.filter_by(checkout_session_id=session.get('id')).first()
            if payment_log:
                payment_log.status = 'completed'
                payment_log.payment_intent_id = session.get('payment_intent')
                payment_log.description = f'Payment successful for booking #{booking.id} (via webhook)'
                if payment_details:
                    payment_log.payment_method = payment_details.get('mode_of_payment')
                if route and route.bus:
                    payment_log.bus_number = route.bus.bus_number
                    payment_log.bus_type = route.bus.bus_type

            ticket_data = None
            try:
                ticket_data = _save_ticket_blob(booking, payment_details)
            except Exception as e:
                current_app.logger.error(f"Webhook: failed to save ticket blob for booking {booking.id}: {e}")
            try:
                _save_completed_payment(booking, payment_details, ticket_data)
            except Exception as e:
                current_app.logger.error(f"Webhook: failed to save completed_payment for booking {booking.id}: {e}")
            try:
                _save_ticket_document(booking, ticket_data)
            except Exception as e:
                current_app.logger.error(f"Webhook: failed to save ticket document for booking {booking.id}: {e}")
            _release_expired_locks_for_booking(booking)

            db.session.commit()

        elif event['type'] == 'checkout.session.expired':
            session = event['data']['object']
            booking_id = session.get('metadata', {}).get('booking_id')
            if not booking_id:
                return

            booking = Booking.query.get(int(booking_id))
            if booking and booking.status == 'booked':
                booking.status = 'cancelled'
                booking.payment_status = 'failed'

                seat_nums = [int(s) for s in booking.seat_numbers.split(',')]
                seats = SeatStatus.query.filter(
                    SeatStatus.route_id == booking.route_id,
                    SeatStatus.seat_number.in_(seat_nums),
                    SeatStatus.booking_id == booking.id,
                ).all()
                for seat in seats:
                    seat.booking_id = None

                payment_log = PaymentLog.query.filter_by(checkout_session_id=session.get('id')).first()
                if payment_log:
                    payment_log.status = 'fail'
                    payment_log.description = f'Payment session expired for booking #{booking.id}'

                db.session.commit()

    except Exception as e:
        db.session.rollback()
        raise BookingError(f'Webhook processing failed: {str(e)}', 500) from e


def _extract_payment_details(payment_intent):
    details = {}
    charges = payment_intent.get('charges', {}).get('data', [])
    if charges:
        pmd = charges[0].get('payment_method_details', {})
        pm_type = pmd.get('type')
        details['mode_of_payment'] = pm_type
        if pm_type == 'card':
            card = pmd.get('card', {})
            brand = (card.get('brand') or '').title()
            last4 = card.get('last4', '****')
            details['bank_details'] = f'{brand} ending in {last4}'
        elif pm_type == 'upi':
            upi = pmd.get('upi', {})
            details['bank_details'] = f"UPI: {upi.get('vpa', 'N/A')}"
    return details


def _build_ticket_data(booking, payment_details=None):
    route = Route.query.get(booking.route_id)
    passengers = PassengerDetail.query.filter_by(booking_id=booking.id).all()

    passenger_list = []
    for p in passengers:
        bd = p.birth_date
        passenger_list.append({
            'seat_number': p.seat_number,
            'name': p.name,
            'age': p.age,
            'gender': p.gender,
            'phone': p.phone,
            'email': p.email,
            'birth_date': bd.isoformat() if bd else None,
            'boarding_location': p.boarding_location,
            'medications': p.medications,
        })

    user = User.query.get(booking.user_id)
    customer_name = booking.passenger_name or (user.name if user else None)

    seat_list = [s.strip() for s in booking.seat_numbers.split(',')]

    bus_number = route.bus.bus_number if route and route.bus else 'N/A'
    bus_type = route.bus.bus_type if route and route.bus else 'N/A'

    source = route.source if route else 'N/A'
    destination = route.destination if route else 'N/A'
    departure_time = route.departure_time if route else None
    arrival_time = route.arrival_time if route else None

    ref_id = booking.reference_id or booking.transaction_id or booking.checkout_session_id
    if not ref_id or len(ref_id) != 8:
        ref_id = _generate_reference_id()
        booking.reference_id = ref_id

    ticket_data = {
        'ticket_id': f'TKT-{booking.id:06d}',
        'booking_id': booking.id,
        'reference_id': ref_id,
        'merchant_name': 'BusBooking',
        'customer_name': customer_name,
        'customer_email': booking.passenger_email or (user.email if user else ''),
        'customer_bank_details': (payment_details or {}).get('bank_details'),
        'mode_of_payment': (payment_details or {}).get('mode_of_payment'),
        'source': source,
        'destination': destination,
        'departure_time': departure_time.isoformat() if departure_time else None,
        'arrival_time': arrival_time.isoformat() if arrival_time else None,
        'formatted_departure_date': departure_time.strftime('%d %b %Y') if departure_time else '',
        'formatted_departure_time': departure_time.strftime('%I:%M %p').lstrip('0') if departure_time else '',
        'formatted_arrival_time': arrival_time.strftime('%I:%M %p').lstrip('0') if arrival_time else '',
        'bus_number': bus_number,
        'bus_type': bus_type,
        'seat_numbers': booking.seat_numbers,
        'seat_numbers_list': seat_list,
        'total_amount': f'INR {booking.total_amount:.2f}',
        'total_amount_value': booking.total_amount,
        'formatted_fare': f'INR {booking.total_amount:.2f}',
        'currency': 'INR',
        'payment_status': booking.payment_status,
        'booking_date': booking.booking_date.isoformat() if booking.booking_date else None,
        'booking_date_formatted': booking.booking_date.strftime('%d %b %Y %I:%M %p').lstrip('0') if booking.booking_date else '',
        'passengers': passenger_list,
        'status': booking.status,
    }
    return ticket_data


def _save_ticket_blob(booking, payment_details=None):
    ticket_data = _build_ticket_data(booking, payment_details)
    existing = Ticket.query.filter_by(booking_id=booking.id).first()
    if existing:
        existing.ticket_data = json.dumps(ticket_data)
    else:
        ticket = Ticket(booking_id=booking.id, ticket_data=json.dumps(ticket_data))
        db.session.add(ticket)
    return ticket_data


def _save_completed_payment(booking, payment_details=None, ticket_data=None):
    current_app.logger.info(f"_save_completed_payment ENTER: booking id={booking.id}, has_ticket={ticket_data is not None}")

    try:
        route = booking.route
        current_app.logger.info(f"  route loaded: {route.id if route else 'None'}")
    except Exception as e:
        current_app.logger.warning(f"  route load failed: {e}")
        route = None
    try:
        passengers = booking.passenger_details or []
        current_app.logger.info(f"  passengers loaded: count={len(passengers)}")
    except Exception as e:
        current_app.logger.warning(f"  passengers load failed: {e}")
        passengers = []
    passenger_list = []
    for p in (passengers or []):
        try:
            passenger_list.append({
                'seat_number': p.seat_number,
                'name': p.name,
                'age': p.age,
                'gender': p.gender,
                'phone': p.phone,
                'boarding_location': p.boarding_location,
                'medications': p.medications,
            })
        except Exception:
            passenger_list.append({'seat_number': None, 'name': str(p)})

    bus_number = route.bus.bus_number if route and route.bus else None
    bus_type = route.bus.bus_type if route and route.bus else None
    payment_method = (payment_details or {}).get('mode_of_payment') if payment_details else None

    existing_payment_log = PaymentLog.query.filter_by(booking_id=booking.id).first()
    if not existing_payment_log:
        current_app.logger.warning(
            f"No PaymentLog found for booking {booking.id} during completed_payment save. "
            "Creating a recovery PaymentLog entry."
        )
        recovery_log = PaymentLog(
            booking_id=booking.id,
            amount=booking.total_amount or 0,
            currency='INR',
            status='completed',
            description=f'Recovery: completed payment for booking #{booking.id}',
            bus_number=bus_number,
            bus_type=bus_type,
        )
        db.session.add(recovery_log)

    try:
        existing_cp = booking.completed_payments
        current_app.logger.info(f"  booking.completed_payments = {existing_cp} (bool={bool(existing_cp)}, type={type(existing_cp).__name__}, len={len(existing_cp) if hasattr(existing_cp, '__len__') else 'N/A'})")
        if existing_cp:
            current_app.logger.info(f"  EARLY RETURN: booking {booking.id} already has {len(existing_cp)} completed payment(s)")
            return
    except Exception as e:
        current_app.logger.error(f"  ERROR checking booking.completed_payments (will still attempt creation): {e}")

    ticket_data_str = json.dumps(ticket_data) if ticket_data else None

    booking.payment_status = 'paid'
    current_app.logger.info(f"  booking.payment_status set to 'paid'")

    try:
        passenger_name = booking.passenger_name or (booking.user.name if booking.user else None)
        passenger_email = booking.passenger_email or (booking.user.email if booking.user else None)
        passenger_phone = booking.passenger_phone or (booking.user.phone if booking.user else None)
    except Exception:
        passenger_name = booking.passenger_name
        passenger_email = None
        passenger_phone = None

    record = CompletedPayment(
        booking_id=booking.id,
        user_id=booking.user_id,
        reference_id=booking.reference_id,
        payment_ref_id=booking.payment_intent_id or booking.transaction_id,
        total_amount=booking.total_amount,
        currency='INR',
        status=booking.payment_status or 'paid',
        seat_numbers=booking.seat_numbers,
        passenger_name=passenger_name,
        passenger_email=passenger_email,
        passenger_phone=passenger_phone,
        route_source=route.source if route else None,
        route_destination=route.destination if route else None,
        bus_number=bus_number,
        bus_type=bus_type,
        payment_method=payment_method,
        passenger_details=json.dumps(passenger_list) if passenger_list else '[]',
        ticket_data=ticket_data_str,
    )
    db.session.add(record)


def _release_expired_locks_for_booking(booking):
    seat_nums = [int(s) for s in booking.seat_numbers.split(',')]
    locks = SeatLock.query.filter(
        SeatLock.route_id == booking.route_id,
        SeatLock.seat_number.in_(seat_nums),
    ).all()
    for lock in locks:
        db.session.delete(lock)


def _generate_qr_data_url(url):
    qr = qrcode.make(url, box_size=4, border=1)  # type: ignore[reportUndefinedVariable]
    buf = io.BytesIO()
    qr.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'data:image/png;base64,{b64}'


def _save_ticket_document(booking, ticket_data, host_url=None):
    existing = TicketDocument.query.filter_by(booking_id=booking.id).first()
    if existing:
        return existing.base64_id

    base64_id = secrets.token_urlsafe(16)
    pdf_url = f'{host_url or ""}api/ticket/pdf/{base64_id}'

    td = ticket_data or _build_ticket_data(booking)
    ticket_html = build_ticket_html(td, pdf_url=pdf_url)

    pdf_css = '''
    .ticket { background: #0f2444; border-radius: 12px; overflow: hidden; font-family: 'Segoe UI', Arial, sans-serif; position: relative; }
    .main { padding: 20px 20px 0; color: #fff; }
    .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; background: none; border: none; padding: 0; }
    .brand { font-size: 10px; font-weight: 600; letter-spacing: 2px; opacity: 0.55; text-transform: uppercase; color: #fff; }
    .route { font-size: 22px; font-weight: 600; color: #fff; margin: 4px 0 2px; }
    .route span { color: #4fc3f7; }
    .badge { background: #4fc3f7; color: #0f2444; font-size: 9px; font-weight: 700; padding: 4px 10px; border-radius: 20px; white-space: nowrap; }
    .info-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 12px; padding-bottom: 4px; }
    .info-item .label { font-size: 8px; letter-spacing: 1px; opacity: 0.5; text-transform: uppercase; margin-bottom: 3px; color: #fff; }
    .info-item .value { font-size: 14px; font-weight: 600; color: #fff; }
    .stub { padding: 12px 20px 16px; background: #0a1a30; color: #fff; }
    .stub-row { display: flex; gap: 16px; align-items: stretch; }
    .stub-label { font-size: 8px; letter-spacing: 1px; color: rgba(255,255,255,0.45); text-transform: uppercase; margin-bottom: 2px; }
    .stub-seat { font-size: 22px; font-weight: 600; color: #4fc3f7; }
    .barcode-id { font-size: 8px; opacity: 0.35; margin-top: 8px; letter-spacing: 1px; text-align: center; color: #fff; }
    body { margin: 0; padding: 16px; background: #0d1f38; font-family: 'Segoe UI', Arial, sans-serif; }
    .ticket-wrapper { max-width: 400px; margin: 0 auto; }
    .qr-section { text-align: center; margin-top: 8px; }
    .qr-section img { width: 80px; height: 80px; }
    .qr-label { font-size: 7px; opacity: 0.4; color: #fff; margin-top: 2px; letter-spacing: 0.5px; }
    '''
    html = f'''<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Ticket - {booking.reference_id or booking.id}</title>
<style>{pdf_css}</style>
</head>
<body><div class="ticket-wrapper">{ticket_html}</div></body>
</html>'''

    # Create the TicketDocument record first so base64_id is always persisted
    # even if PDF generation fails. pdf_blob will be None initially.
    doc = TicketDocument(
        booking_id=booking.id,
        pdf_blob=None,
        base64_id=base64_id,
    )
    db.session.add(doc)
    db.session.flush()

    # Try to generate the PDF and update the blob
    try:
        from xhtml2pdf import pisa
        buf = io.BytesIO()
        result = pisa.CreatePDF(html, dest=buf)
        if not result.err:
            doc.pdf_blob = buf.getvalue()
        else:
            current_app.logger.error(
                f"Ticket PDF generation failed (pisa errors) for booking {booking.id}: {result.err}"
            )
    except Exception as e:
        current_app.logger.error(
            f"Ticket PDF generation failed (xhtml2pdf error) for booking {booking.id}: {e}"
        )

    return base64_id



def get_ticket_data(booking_id, current_user):
    try:
        booking = Booking.query.get(booking_id)
        if not booking:
            raise BookingError('Booking not found', 404)

        # If user is not authenticated, block access.
        if not current_user:
            raise BookingError('Unauthorized', 403)

        # Allow only booking owner, admin, or driver.
        if booking.user_id != current_user.id and current_user.role not in ('admin', 'driver'):
            raise BookingError('Unauthorized', 403)

        # Look up stored payment details from CompletedPayment
        payment_details = None
        cp = booking.completed_payments
        if cp:
            try:
                cp_record = cp[0] if isinstance(cp, list) else cp
                pm = getattr(cp_record, 'payment_method', None)
                if pm:
                    payment_details = {'mode_of_payment': pm}
                    # Try to get bank_details from the stored ticket_data
                    td_raw = getattr(cp_record, 'ticket_data', None)
                    if td_raw:
                        try:
                            stored_td = json.loads(td_raw) if isinstance(td_raw, str) else td_raw
                            bd = stored_td.get('customer_bank_details')
                            if bd:
                                payment_details['bank_details'] = bd
                        except Exception:
                            pass
            except Exception:
                pass

        if not payment_details:
            # Fallback: check PaymentLog
            pl = PaymentLog.query.filter_by(booking_id=booking.id).order_by(PaymentLog.created_at.desc()).first()
            if pl and pl.payment_method:
                payment_details = {'mode_of_payment': pl.payment_method}

        ticket_data = _build_ticket_data(booking, payment_details=payment_details)

        return {'message': 'Ticket retrieved successfully', 'ticket': ticket_data}

    except BookingError:
        raise
    except Exception as e:
        raise BookingError(f'Failed to get ticket: {str(e)}', 500) from e


def get_bus_manifest(route_id, current_user):
    try:
        route = Route.query.get(route_id)
        if not route:
            raise BookingError('Route not found', 404)

        if current_user.role == 'driver' and route.driver_id != current_user.id:
            raise BookingError('Unauthorized: this route is not assigned to you', 403)

        if current_user.role not in ('admin', 'driver'):
            raise BookingError('Unauthorized', 403)

        if route.available_seats > 0:
            raise BookingError(
                'Bus is not yet full. Manifest available only when all seats are booked.',
                400,
            )

        all_seats = SeatStatus.query.filter_by(route_id=route.id).order_by(SeatStatus.seat_number).all()
        driver_name = route.driver.name if route.driver else 'Not assigned'

        seat_manifest = []
        for seat in all_seats:
            passenger_info = None
            if seat.is_booked and seat.booking_id:
                b = Booking.query.get(seat.booking_id)
                if b:
                    pd = PassengerDetail.query.filter_by(booking_id=b.id, seat_number=seat.seat_number).first()
                    if pd:
                        passenger_info = {
                            'name': pd.name,
                            'age': pd.age,
                            'gender': pd.gender,
                            'phone': pd.phone,
                        }
                    else:
                        passenger_info = {
                            'name': b.passenger_name,
                            'age': None,
                            'gender': None,
                            'phone': b.passenger_phone,
                        }

            seat_manifest.append({
                'seat_number': seat.seat_number,
                'seat_type': seat.seat_type,
                'is_booked': seat.is_booked,
                'passenger': passenger_info,
            })

        return {
            'bus_id': route.bus.id,
            'bus_number': route.bus.bus_number,
            'bus_type': route.bus.bus_type,
            'source': route.source,
            'destination': route.destination,
            'departure_time': route.departure_time.isoformat(),
            'arrival_time': route.arrival_time.isoformat(),
            'driver_name': driver_name,
            'total_seats': route.bus.total_seats,
            'available_seats': route.available_seats,
            'seats': seat_manifest,
        }

    except BookingError:
        raise
    except Exception as e:
        raise BookingError(f'Failed to get bus manifest: {str(e)}', 500) from e


def build_ticket_html(td, pdf_url=None):
    if not td:
        return '<p style="text-align:center;color:#6b7280;">Ticket data not available.</p>'

    seats = ', '.join(td.get('seat_numbers_list', [])) or td.get('seat_numbers', '')
    ref_id = td.get('reference_id', 'N/A')
    customer_name = td.get('customer_name', 'N/A')
    customer_email = td.get('customer_email', '')
    source = td.get('source', '')
    destination = td.get('destination', '')
    bus_type = td.get('bus_type', '')
    bus_number = td.get('bus_number', '')
    dep_date = td.get('formatted_departure_date', '')
    dep_time = td.get('formatted_departure_time', '')
    arr_time = td.get('formatted_arrival_time', '')
    total_amount = td.get('total_amount', 'N/A')
    booking_date = td.get('booking_date_formatted', '')
    passengers = td.get('passengers') or []

    qr_html = ''
    if pdf_url:
        qr_data_url = _generate_qr_data_url(pdf_url)
        qr_html = f'''
    <div class="qr-section">
      <img src="{qr_data_url}" alt="QR Code" width="80" height="80" />
      <div class="qr-label">Scan to download ticket PDF</div>
    </div>'''

    passenger_list_html = ''
    for p in passengers:
        age_str = ''
        if p.get('age'):
            age_str = f'{p["age"]}y'
        if p.get('gender'):
            age_str += f', {p["gender"]}' if age_str else p['gender']
        extra = ''
        if p.get('boarding_location'):
            extra = f'<span style="opacity:0.5;font-size:10px;"> Board: {p["boarding_location"]}</span>'
        passenger_list_html += f'''
            <div style="padding:4px 0;border-bottom:1px solid rgba(79,195,247,0.15);display:flex;justify-content:space-between;font-size:12px;">
                <span>{p.get("name","")} <span style="opacity:0.5;">{age_str}</span>{extra}</span>
                <span style="color:#4fc3f7;">Seat {p.get("seat_number","")}</span>
            </div>'''

    ticket_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bus Ticket - {ref_id}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1f38; display: flex; justify-content: center; align-items: center; min-height: 100vh; font-family: 'Segoe UI', Arial, sans-serif; }}
  .ticket {{ width: 420px; background: #0f2444; border-radius: 16px; overflow: hidden; }}
  .main {{ padding: 24px 24px 0 24px; color: #fff; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 18px; }}
  .brand {{ font-size: 11px; font-weight: 600; letter-spacing: 2px; opacity: 0.55; text-transform: uppercase; }}
  .route {{ font-size: 28px; font-weight: 600; color: #fff; margin: 6px 0 4px; }}
  .route span {{ color: #4fc3f7; }}
  .badge {{ background: #4fc3f7; color: #0f2444; font-size: 10px; font-weight: 700; padding: 5px 12px; border-radius: 20px; letter-spacing: 0.5px; }}
  .info-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; margin-top: 16px; padding-bottom: 6px; }}
  .info-item .label {{ font-size: 9px; letter-spacing: 1px; opacity: 0.5; text-transform: uppercase; margin-bottom: 4px; }}
  .info-item .value {{ font-size: 15px; font-weight: 600; color: #fff; }}
  .perf {{ display: flex; align-items: center; position: relative; height: 26px; margin: 6px 0; }}
  .perf-line {{ flex: 1; border-top: 2px dashed rgba(79,195,247,0.3); }}
  .perf-circle-l {{ width: 22px; height: 22px; border-radius: 50%; background: #0d1f38; position: absolute; left: -11px; }}
  .perf-circle-r {{ width: 22px; height: 22px; border-radius: 50%; background: #0d1f38; position: absolute; right: -11px; }}
  .stub {{ padding: 14px 24px 18px; background: #0a1a30; color: #fff; }}
  .stub-row {{ display: flex; justify-content: space-between; align-items: center; }}
  .stub-label {{ font-size: 9px; letter-spacing: 1px; color: rgba(255,255,255,0.45); text-transform: uppercase; margin-bottom: 3px; }}
  .stub-seat {{ font-size: 26px; font-weight: 600; color: #4fc3f7; }}
  .barcode {{ display: flex; gap: 2px; margin-top: 12px; }}
  .barcode-id {{ font-size: 9px; opacity: 0.35; margin-top: 5px; letter-spacing: 1px; }}
</style>
</head>
<body>
<div class="ticket">
  <div class="main">
    <div class="header">
      <div>
        <div class="brand">BusBooking</div>
        <div class="route">{source} <span>\u2192</span> {destination}</div>
      </div>
      <div class="badge">{bus_type or 'Express'}</div>
    </div>
    <div class="info-grid">
      <div class="info-item"><div class="label">Date</div><div class="value">{dep_date}</div></div>
      <div class="info-item"><div class="label">Departs</div><div class="value">{dep_time}</div></div>
      <div class="info-item"><div class="label">Arrives</div><div class="value">{arr_time}</div></div>
      <div class="info-item"><div class="label">Seat</div><div class="value">{seats}</div></div>
      <div class="info-item"><div class="label">Class</div><div class="value">{bus_type or 'Standard'}</div></div>
      <div class="info-item"><div class="label">Fare</div><div class="value">{total_amount}</div></div>
    </div>
  </div>
  <div class="perf">
    <div class="perf-circle-l"></div>
    <div class="perf-line"></div>
    <div class="perf-circle-r"></div>
  </div>
  <div class="stub">
    <div class="stub-row">
      <div><div class="stub-label">Seat No.</div><div class="stub-seat">{seats}</div></div>
      <div style="text-align:right"><div class="stub-label">Passenger</div><div style="font-size:14px;font-weight:600;color:#fff">{customer_name}</div></div>
    </div>
    {passenger_list_html}
    <div class="barcode-id">{ref_id}</div>
    {qr_html}
  </div>
</div>
</body>
</html>'''

    return ticket_html



def build_premium_ticket_html(td):
    """Generate premium-design ticket HTML matching the frontend TicketView component."""
    if not td:
        return '<p style="text-align:center;color:#6b7280;">Ticket data not available.</p>'

    seats = ', '.join(td.get('seat_numbers_list', [])) or td.get('seat_numbers', 'N/A')
    ref_id = td.get('reference_id', 'N/A')
    customer_name = td.get('customer_name', 'N/A')
    source = td.get('source', '')
    destination = td.get('destination', '')
    bus_type = td.get('bus_type', 'Standard')
    bus_number = td.get('bus_number', '')
    dep_date = td.get('formatted_departure_date', '')
    dep_time = td.get('formatted_departure_time', '')
    arr_time = td.get('formatted_arrival_time', '')
    total_amount = td.get('total_amount_value', 0)
    booking_id = td.get('booking_id', 0)
    status = td.get('status', 'booked')
    payment_status = td.get('payment_status', 'succeeded')
    passengers = td.get('passengers') or []
    ticket_id = td.get('ticket_id', 'TKT-' + str(booking_id).zfill(6))

    duration = ''
    if td.get('departure_time') and td.get('arrival_time'):
        try:
            from datetime import datetime as _dt
            dep = _dt.fromisoformat(td['departure_time'].replace('Z', '+00:00'))
            arr = _dt.fromisoformat(td['arrival_time'].replace('Z', '+00:00'))
            diff = arr - dep
            total_mins = int(diff.total_seconds() / 60)
            hours = total_mins // 60
            mins = total_mins % 60
            duration = str(hours) + ' Hour' + ('s' if hours != 1 else '')
            if mins:
                duration += ' ' + str(mins) + ' Min'
        except Exception:
            pass

    source_parts = [s.strip() for s in source.split(',')]
    dest_parts = [s.strip() for s in destination.split(',')]

    passenger_rows = ''
    for p in passengers:
        age_str = ''
        if p.get('age'):
            age_str = str(p['age']) + 'y'
        if p.get('gender'):
            age_str += (', ' + p['gender']) if age_str else p['gender']
        passenger_rows += (
            '<tr>'
            '<td style="padding:6px 0;font-size:12px;">' + str(p.get('name', 'N/A')) + ' '
            '<span style="color:#94a3b8;">' + age_str + '</span></td>'
            '<td style="padding:6px 0;font-size:12px;color:#2563eb;text-align:right;">'
            'Seat ' + str(p.get('seat_number', '')) + '</td></tr>'
        )

    if not passenger_rows:
        passenger_rows = (
            '<tr>'
            '<td style="padding:6px 0;font-size:12px;">' + customer_name + '</td>'
            '<td style="padding:6px 0;font-size:12px;color:#2563eb;text-align:right;">' + seats + '</td>'
            '</tr>'
        )

    base_fare = int(total_amount * 0.84)
    taxes = int(total_amount * 0.11)
    conv_fee = int(total_amount * 0.05)
    status_color = '#22c55e' if status == 'booked' else '#ef4444'
    status_label = 'CONFIRMED' if status == 'booked' else status.upper()
    src_sub = ', '.join(source_parts[1:]) if len(source_parts) > 1 else ''
    dst_sub = ', '.join(dest_parts[1:]) if len(dest_parts) > 1 else ''
    duration_html = '<div class="duration">' + duration + '</div>' if duration else ''
    paid_label = '&#10003; Paid' if payment_status in ('succeeded', 'paid') else payment_status

    src_city = source_parts[0] if source_parts else source
    dst_city = dest_parts[0] if dest_parts else destination
    src_small = '<small>' + src_sub + '</small>' if src_sub else ''
    dst_small = '<small>' + dst_sub + '</small>' if dst_sub else ''

    html = (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<title>Bus Ticket - ' + ref_id + '</title>\n'
        '<style>\n'
        '@page { size: A4; margin: 20mm; }\n'
        '* { box-sizing: border-box; margin: 0; padding: 0; }\n'
        'body { font-family: "Segoe UI", Arial, sans-serif; background: #eef2f7; color: #1e293b; }\n'
        '.ticket { max-width: 800px; margin: 0 auto; background: white; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,.08); }\n'
        '.header { background: linear-gradient(135deg, #0f172a, #1e3a8a); color: white; padding: 20px 28px; display: flex; justify-content: space-between; align-items: center; }\n'
        '.header h1 { font-size: 22px; font-weight: 700; }\n'
        '.header p { opacity: .8; font-size: 12px; }\n'
        '.badge { display: inline-block; background: ' + status_color + '; padding: 6px 14px; border-radius: 50px; font-size: 11px; font-weight: 700; letter-spacing: 1px; color: white; }\n'
        '.route { padding: 28px; background: #f8fafc; display: flex; align-items: center; justify-content: space-between; }\n'
        '.city { text-align: center; }\n'
        '.city h2 { font-size: 26px; font-weight: 700; }\n'
        '.city small { color: #64748b; font-size: 11px; }\n'
        '.city .time { font-size: 15px; font-weight: 600; margin-top: 4px; }\n'
        '.route-center { flex: 1; padding: 0 30px; text-align: center; }\n'
        '.duration { font-size: 12px; font-weight: 600; color: #475569; margin-bottom: 8px; }\n'
        '.line { height: 2px; background: #cbd5e1; position: relative; }\n'
        '.line::before, .line::after { content: ""; width: 10px; height: 10px; border-radius: 50%; background: #2563eb; position: absolute; top: -4px; }\n'
        '.line::before { left: 0; }\n'
        '.line::after { right: 0; }\n'
        '.content { padding: 24px; }\n'
        '.cards { display: flex; gap: 16px; margin-bottom: 20px; }\n'
        '.card { flex: 1; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; }\n'
        '.card-header { background: #f8fafc; padding: 10px 16px; font-weight: 700; font-size: 12px; border-bottom: 1px solid #e2e8f0; }\n'
        '.card-body { padding: 16px; }\n'
        '.info-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px dashed #e2e8f0; font-size: 12px; }\n'
        '.info-row:last-child { border-bottom: none; }\n'
        '.info-row .label { color: #64748b; }\n'
        '.info-row .value { font-weight: 600; }\n'
        '.bottom { display: flex; gap: 16px; }\n'
        '.fare { flex: 1; border-radius: 12px; background: #0f172a; color: white; padding: 20px; }\n'
        '.fare h3 { font-size: 14px; font-weight: 700; margin-bottom: 12px; }\n'
        '.fare-row { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 13px; }\n'
        '.fare-row span:first-child { opacity: .8; }\n'
        '.total { margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(255,255,255,.2); font-size: 22px; font-weight: bold; text-align: right; }\n'
        '.qr-section { width: 200px; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; text-align: center; }\n'
        '.qr-section h3 { font-size: 12px; font-weight: 700; margin-bottom: 10px; }\n'
        '.qr-box { width: 130px; height: 130px; background: #f1f5f9; margin: 0 auto; border-radius: 8px; display: flex; align-items: center; justify-content: center; }\n'
        '.qr-box img { width: 120px; height: 120px; }\n'
        '.qr-label { font-size: 10px; color: #64748b; margin-top: 8px; }\n'
        '.notice { margin: 0 24px 20px; background: #fff7ed; border-left: 4px solid #f97316; padding: 12px; border-radius: 6px; font-size: 11px; color: #9a3412; }\n'
        '.footer { background: #f8fafc; padding: 16px 28px; border-top: 1px solid #e2e8f0; display: flex; justify-content: space-between; font-size: 11px; color: #475569; }\n'
        '.footer div strong { display: block; margin-bottom: 2px; }\n'
        '</style>\n</head>\n<body>\n'
        '<div class="ticket">\n'
        '  <div class="header">\n'
        '    <div><h1>&#x1F68C; BusBooking</h1><p>Premium E-Ticket</p></div>\n'
        '    <div style="text-align:right"><span class="badge">' + status_label + '</span><p style="margin-top:4px;">Booking ID: #' + str(booking_id) + '</p></div>\n'
        '  </div>\n'
        '  <div class="route">\n'
        '    <div class="city"><h2>' + src_city + '</h2>' + src_small + '<div class="time">' + dep_time + '</div></div>\n'
        '    <div class="route-center">' + duration_html + '<div class="line"></div></div>\n'
        '    <div class="city"><h2>' + dst_city + '</h2>' + dst_small + '<div class="time">' + arr_time + '</div></div>\n'
        '  </div>\n'
        '  <div class="content">\n'
        '    <div class="cards">\n'
        '      <div class="card"><div class="card-header">Journey Details</div><div class="card-body">\n'
        '        <div class="info-row"><span class="label">Ticket ID</span><span class="value">' + ticket_id + '</span></div>\n'
        '        <div class="info-row"><span class="label">PNR</span><span class="value">' + ref_id + '</span></div>\n'
        '        <div class="info-row"><span class="label">Journey Date</span><span class="value">' + dep_date + '</span></div>\n'
        '        <div class="info-row"><span class="label">Bus Type</span><span class="value">' + bus_type + '</span></div>\n'
        '        <div class="info-row"><span class="label">Bus Number</span><span class="value">' + bus_number + '</span></div>\n'
        '        <div class="info-row"><span class="label">Seat Number</span><span class="value">' + seats + '</span></div>\n'
        '      </div></div>\n'
        '      <div class="card"><div class="card-header">Passenger Information</div><div class="card-body">\n'
        '        <table style="width:100%;border-collapse:collapse;">' + passenger_rows + '</table>\n'
        '      </div></div>\n'
        '    </div>\n'
        '    <div class="bottom">\n'
        '      <div class="fare"><h3>Fare Summary</h3>\n'
        '        <div class="fare-row"><span>Base Fare</span><span>&#8377;' + str(base_fare) + '</span></div>\n'
        '        <div class="fare-row"><span>Taxes</span><span>&#8377;' + str(taxes) + '</span></div>\n'
        '        <div class="fare-row"><span>Convenience Fee</span><span>&#8377;' + str(conv_fee) + '</span></div>\n'
        '        <div class="total">&#8377;' + '{:,.0f}'.format(total_amount) + '</div>\n'
        '      </div>\n'
        '      <div class="qr-section"><h3>Ticket Verification</h3>\n'
        '        <div class="qr-box"><img src="https://api.qrserver.com/v1/create-qr-code/?size=120x120&data=' + ref_id + '&bgcolor=transparent" alt="QR" /></div>\n'
        '        <div class="qr-label">Scan during boarding verification</div>\n'
        '      </div>\n'
        '    </div>\n'
        '  </div>\n'
        '  <div class="notice">Please arrive at the boarding point at least 30 minutes before departure and carry a valid government-issued photo ID.</div>\n'
        '  <div class="footer">\n'
        '    <div><strong>Support</strong>support@busbooking.com</div>\n'
        '    <div><strong>Passenger</strong>' + customer_name + '</div>\n'
        '    <div><strong>Boarding Point</strong>' + source + ' Bus Stand</div>\n'
        '    <div><strong>Payment</strong>' + paid_label + '</div>\n'
        '  </div>\n'
        '</div>\n'
        '</body>\n</html>'
    )
    return html
