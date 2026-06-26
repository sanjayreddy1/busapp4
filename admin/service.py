import base64
import csv
import io
import json
import secrets
from datetime import datetime

import stripe
from sqlalchemy.exc import IntegrityError

from ..core.auth import format_indian_phone, validate_indian_phone
from ..core.extensions import db, stripe as stripe_module
from ..core.models import AgencyProfile, Booking, Bus, BusExportDocument, PaymentLog, Route, SeatStatus, User


class AdminError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def get_dashboard_stats():
    try:
        total_users = User.query.count()
        total_bookings = Booking.query.count()
        confirmed_bookings = Booking.query.filter_by(status='booked').count()
        cancelled_bookings = Booking.query.filter_by(status='cancelled').count()
        total_revenue = db.session.query(db.func.sum(Booking.total_amount)).filter(Booking.payment_status.in_(['paid', 'succeeded'])).scalar() or 0
        pending_payments = Booking.query.filter_by(payment_status='pending').count()
        total_routes = Route.query.filter_by(status='active').count()
        total_buses = Bus.query.filter_by(is_active=True).count()

        route_stats = db.session.query(
            Route.source,
            Route.destination,
            db.func.count(Booking.id).label('booking_count'),
            db.func.sum(Booking.total_amount).label('revenue')
        ).join(Booking).filter(Booking.status == 'booked').group_by(Route.id).all()

        recent_bookings = Booking.query.order_by(Booking.booking_date.desc()).limit(10).all()

        return {
            'message': 'Dashboard stats retrieved successfully',
            'statistics': {
                'total_users': total_users,
                'total_bookings': total_bookings,
                'booked_bookings': confirmed_bookings,
                'cancelled_bookings': cancelled_bookings,
                'total_routes': total_routes,
                'total_buses': total_buses,
                'total_revenue': f"INR {float(total_revenue):.2f}",
                'total_revenue_value': float(total_revenue),
                'pending_payments': pending_payments
            },
            'route_statistics': [{
                'route': f"{stat.source} to {stat.destination}",
                'booking_count': stat.booking_count,
                'revenue': f"INR {float(stat.revenue):.2f}" if stat.revenue else 'INR 0.00',
                'revenue_value': float(stat.revenue) if stat.revenue else 0
            } for stat in route_stats],
            'recent_bookings': [{
                'booking_id': b.id,
                'user_name': b.user.name,
                'user_email': b.user.email,
                'user_phone': b.user.phone,
                'total_amount': f"INR {b.total_amount:.2f}",
                'total_amount_value': b.total_amount,
                'status': b.status,
                'booking_date': b.booking_date.isoformat()
            } for b in recent_bookings]
        }

    except Exception as e:
        raise AdminError(f'Failed to fetch dashboard stats: {str(e)}', 500) from e


def get_all_tickets(status=None, date_from=None, date_to=None, source=None, destination=None):
    try:
        query = Booking.query

        if status:
            query = query.filter(Booking.status == status)

        if date_from:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(Booking.booking_date >= date_from_obj)

        if date_to:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            query = query.filter(Booking.booking_date <= date_to_obj)

        if source:
            query = query.join(Route).filter(Route.source.ilike(f'%{source}%'))

        if destination:
            query = query.join(Route).filter(Route.destination.ilike(f'%{destination}%'))

        bookings = query.order_by(Booking.booking_date.desc()).all()

        result = []
        for booking in bookings:
            route = Route.query.get(booking.route_id)
            result.append({
                'ticket_id': booking.id,
                'user': {
                    'id': booking.user.id,
                    'name': booking.user.name,
                    'email': booking.user.email,
                    'phone': booking.user.phone
                },
                'route': {
                    'source': route.source,
                    'destination': route.destination,
                    'departure_time': route.departure_time.isoformat(),
                    'arrival_time': route.arrival_time.isoformat(),
                    'bus_number': route.bus.bus_number
                },
                'seat_numbers': booking.seat_numbers,
                'total_amount': f"INR {booking.total_amount:.2f}",
                'total_amount_value': booking.total_amount,
                'booking_date': booking.booking_date.isoformat(),
                'status': booking.status,
                'payment_status': booking.payment_status,
                'transaction_id': booking.transaction_id,
                'passenger_name': booking.passenger_name,
                'passenger_phone': booking.passenger_phone
            })

        return {'message': 'Tickets retrieved successfully', 'total_tickets': len(result), 'tickets': result}

    except ValueError as e:
        raise AdminError(f'Invalid date format: {str(e)}', 400) from e
    except Exception as e:
        raise AdminError(f'Failed to fetch tickets: {str(e)}', 500) from e


def update_ticket_status(ticket_id, data):
    try:
        if ticket_id:
            pass
        else:
            raise AdminError('Ticket ID is required', 400)

        booking = Booking.query.get(ticket_id)

        if booking:
            pass
        else:
            raise AdminError('Ticket not found in the database', 404)

        if 'status' in data:
            booking.status = data['status']

        if 'special_requests' in data:
            booking.special_requests = data['special_requests']

        if 'passenger_name' in data:
            booking.passenger_name = data['passenger_name']

        if 'passenger_email' in data:
            booking.passenger_email = data['passenger_email']

        if 'passenger_phone' in data:
            if data['passenger_phone']:
                if not validate_indian_phone(data['passenger_phone']):
                    raise AdminError('Invalid Indian phone number', 400)
                booking.passenger_phone = format_indian_phone(data['passenger_phone'])

        if 'status' in data and data['status'] == 'cancelled' and booking.status != 'cancelled':
            route = Route.query.get(booking.route_id)
            seat_numbers = [int(s) for s in booking.seat_numbers.split(',')]

            for seat_num in seat_numbers:
                seat = SeatStatus.query.filter_by(route_id=booking.route_id, seat_number=seat_num).first()
                if seat:
                    seat.is_booked = False
                    seat.booked_by = None
                    seat.booking_id = None

            route.available_seats += len(seat_numbers)
            booking.payment_status = 'refunded'

            try:
                stripe_module.Refund.create(payment_intent=booking.payment_intent_id, amount=int(booking.total_amount * 100))
            except stripe.error.StripeError as e:
                raise AdminError(f'Refund failed: {str(e)}', 400) from e

        booking.updated_at = datetime.utcnow()
        db.session.commit()

        return {
            'id': booking.id,
            'status': booking.status,
            'passenger_name': booking.passenger_name,
            'passenger_phone': booking.passenger_phone,
            'special_requests': booking.special_requests,
            'updated_at': booking.updated_at.isoformat()
        }

    except AdminError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Ticket update failed: {str(e)}', 500) from e


def manage_routes_get():
    try:
        routes = Route.query.all()
        result = []
        for route in routes:
            driver_info = None
            if route.driver:
                driver_info = {
                    'id': route.driver.id,
                    'name': route.driver.name,
                    'phone': route.driver.phone,
                    'license_number': route.driver.license_number,
                }
            result.append({
                'id': route.id,
                'source': route.source,
                'destination': route.destination,
                'departure_time': route.departure_time.isoformat(),
                'arrival_time': route.arrival_time.isoformat(),
                'bus_id': route.bus_id,
                'bus_number': route.bus.bus_number,
                'bus_type': route.bus.bus_type,
                'total_seats': route.bus.total_seats,
                'driver_id': route.driver_id,
                'driver': driver_info,
                'price': f"INR {route.price:.2f}",
                'price_value': route.price,
                'available_seats': route.available_seats,
                'status': route.status
            })
        return result

    except Exception as e:
        raise AdminError(f'Failed to fetch routes: {str(e)}', 500) from e


def manage_routes_create(data):
    try:
        if 'source' in data:
            pass
        else:
            raise AdminError('source is required', 400)

        if 'destination' in data:
            pass
        else:
            raise AdminError('destination is required', 400)

        if 'departure_time' in data:
            pass
        else:
            raise AdminError('departure_time is required', 400)

        if 'arrival_time' in data:
            pass
        else:
            raise AdminError('arrival_time is required', 400)

        if 'bus_id' in data:
            pass
        else:
            raise AdminError('bus_id is required', 400)

        if 'price' in data:
            pass
        else:
            raise AdminError('price is required', 400)

        bus = Bus.query.get(data['bus_id'])
        if bus:
            pass
        else:
            raise AdminError('Bus not found in the database', 404)

        driver_id = data.get('driver_id')
        if driver_id:
            driver = User.query.get(driver_id)
            if not driver or driver.role != 'driver':
                raise AdminError('Invalid driver selected', 400)

        route = Route(
            source=data['source'],
            destination=data['destination'],
            departure_time=datetime.fromisoformat(data['departure_time']),
            arrival_time=datetime.fromisoformat(data['arrival_time']),
            bus_id=data['bus_id'],
            driver_id=driver_id,
            price=data['price'],
            available_seats=bus.total_seats,
            status=data.get('status', 'active')
        )

        db.session.add(route)
        db.session.commit()

        return {'message': 'Route created successfully', 'route_id': route.id}

    except IntegrityError as e:
        db.session.rollback()
        raise AdminError('Database integrity error', 500) from e
    except (ValueError, TypeError) as e:
        db.session.rollback()
        raise AdminError(f'Invalid date/time format: {str(e)}', 400) from e
    except AdminError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Route creation failed: {str(e)}', 500) from e


def patch_route(route_id, data):
    try:
        if route_id:
            pass
        else:
            raise AdminError('Route ID is required', 400)

        route = Route.query.get(route_id)

        if route:
            pass
        else:
            raise AdminError('Route not found in the database', 404)

        if 'source' in data:
            route.source = data['source']

        if 'destination' in data:
            route.destination = data['destination']

        if 'departure_time' in data:
            route.departure_time = datetime.fromisoformat(data['departure_time'])

        if 'arrival_time' in data:
            route.arrival_time = datetime.fromisoformat(data['arrival_time'])

        if 'price' in data:
            route.price = data['price']

        if 'status' in data:
            route.status = data['status']

        if 'cancellation_reason' in data:
            route.cancellation_reason = data['cancellation_reason']

        if 'driver_id' in data:
            driver_id = data['driver_id']
            if driver_id:
                driver = User.query.get(driver_id)
                if not driver or driver.role != 'driver':
                    raise AdminError('Invalid driver selected', 400)
            route.driver_id = driver_id

        if 'status' in data and data['status'] == 'cancelled' and route.status != 'cancelled':
            bookings = Booking.query.filter_by(route_id=route_id, status='booked').all()

            for booking in bookings:
                booking.status = 'cancelled'
                booking.payment_status = 'refunded'

                seat_numbers = [int(s) for s in booking.seat_numbers.split(',')]
                for seat_num in seat_numbers:
                    seat = SeatStatus.query.filter_by(route_id=route_id, seat_number=seat_num).first()
                    if seat:
                        seat.is_booked = False
                        seat.booked_by = None

                try:
                    stripe_module.Refund.create(
                        payment_intent=booking.payment_intent_id,
                        amount=int(booking.total_amount * 100)
                    )
                except stripe.error.StripeError:
                    pass

        route.updated_at = datetime.utcnow()
        db.session.commit()

        driver_info = None
        if route.driver:
            driver_info = {
                'id': route.driver.id,
                'name': route.driver.name,
                'phone': route.driver.phone,
            }

        return {
            'id': route.id,
            'source': route.source,
            'destination': route.destination,
            'departure_time': route.departure_time.isoformat(),
            'arrival_time': route.arrival_time.isoformat(),
            'driver_id': route.driver_id,
            'driver': driver_info,
            'price': f"INR {route.price:.2f}",
            'price_value': route.price,
            'status': route.status,
            'updated_at': route.updated_at.isoformat()
        }

    except (ValueError, TypeError) as e:
        db.session.rollback()
        raise AdminError(f'Invalid date/time format: {str(e)}', 400) from e
    except AdminError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Route update failed: {str(e)}', 500) from e


def create_bus(data):
    try:
        bus_number = data.get('bus_number')
        total_seats = data.get('total_seats')
        bus_type = data.get('bus_type', 'standard')
        amenities = data.get('amenities', '')
        is_active = data.get('is_active', True)

        if not bus_number:
            raise AdminError('bus_number is required', 400)
        if not total_seats:
            raise AdminError('total_seats is required', 400)

        bus = Bus(
            bus_number=bus_number,
            total_seats=int(total_seats),
            bus_type=bus_type,
            amenities=amenities,
            is_active=is_active,
        )
        db.session.add(bus)
        db.session.commit()

        return {
            'message': 'Bus created successfully',
            'bus': {
                'id': bus.id,
                'bus_number': bus.bus_number,
                'total_seats': bus.total_seats,
                'bus_type': bus.bus_type,
                'amenities': bus.amenities,
                'is_active': bus.is_active,
            }
        }

    except IntegrityError as e:
        db.session.rollback()
        raise AdminError('Bus number must be unique', 409) from e
    except AdminError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Bus creation failed: {str(e)}', 500) from e


def patch_bus(bus_id, data):
    try:
        if bus_id:
            pass
        else:
            raise AdminError('Bus ID is required', 400)

        bus = Bus.query.get(bus_id)

        if bus:
            pass
        else:
            raise AdminError('Bus not found in the database', 404)

        if 'bus_number' in data:
            bus.bus_number = data['bus_number']

        if 'total_seats' in data:
            bus.total_seats = data['total_seats']

        if 'amenities' in data:
            bus.amenities = data['amenities']

        if 'bus_type' in data:
            bus.bus_type = data['bus_type']

        if 'is_active' in data:
            bus.is_active = data['is_active']

        bus.updated_at = datetime.utcnow()
        db.session.commit()

        return {
            'id': bus.id,
            'bus_number': bus.bus_number,
            'total_seats': bus.total_seats,
            'amenities': bus.amenities,
            'bus_type': bus.bus_type,
            'is_active': bus.is_active,
            'updated_at': bus.updated_at.isoformat()
        }

    except IntegrityError as e:
        db.session.rollback()
        raise AdminError('Bus number must be unique', 409) from e
    except AdminError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Bus update failed: {str(e)}', 500) from e


def update_payment_status(payment_id, data):
    try:
        if payment_id:
            pass
        else:
            raise AdminError('Payment ID is required', 400)

        payment_log = PaymentLog.query.get(payment_id)

        if payment_log:
            pass
        else:
            raise AdminError('Payment record not found in the database', 404)

        if 'status' in data:
            payment_log.status = data['status']
            payment_log.updated_at = datetime.utcnow()

            if payment_log.booking:
                payment_log.booking.payment_status = data['status']
                payment_log.booking.updated_at = datetime.utcnow()
        else:
            raise AdminError('status field is required', 400)

        db.session.commit()

        return {
            'id': payment_log.id,
            'status': payment_log.status,
            'updated_at': payment_log.updated_at.isoformat()
        }

    except AdminError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Payment status update failed: {str(e)}', 500) from e


def get_all_payments_admin():
    try:
        payment_logs = PaymentLog.query.order_by(PaymentLog.created_at.desc()).all()

        result = []
        for log in payment_logs:
            booking = Booking.query.get(log.booking_id) if log.booking_id else None
            route = Route.query.get(booking.route_id) if booking else None
            user_info = None
            if booking and booking.user:
                user_info = {
                    'id': booking.user.id,
                    'name': booking.user.name,
                    'email': booking.user.email,
                    'phone': booking.user.phone
                }

            result.append({
                'id': log.id,
                'checkout_session_id': log.checkout_session_id,
                'payment_intent_id': log.payment_intent_id,
                'booking_id': log.booking_id,
                'user': user_info,
                'route': {
                    'source': route.source,
                    'destination': route.destination
                } if route else None,
                'amount': f"INR {log.amount:.2f}",
                'amount_value': log.amount,
                'currency': log.currency,
                'status': log.status,
                'description': log.description,
                'payment_method': log.payment_method,
                'booking_status': booking.status if booking else None,
                'created_at': log.created_at.isoformat() if log.created_at else None,
                'updated_at': log.updated_at.isoformat() if log.updated_at else None
            })

        return {'message': 'Payments retrieved successfully', 'total_payments': len(result), 'payments': result}

    except Exception as e:
        raise AdminError(f'Failed to fetch payments: {str(e)}', 500) from e


def get_all_users_admin(role=None):
    try:
        query = User.query
        if role:
            query = query.filter(User.role == role)
        users = query.order_by(User.created_at.desc()).all()

        result = []
        for u in users:
            result.append({
                'id': u.id,
                'name': u.name,
                'email': u.email,
                'phone': u.phone,
                'role': u.role,
                'gender': u.gender,
                'dob': u.dob.isoformat() if u.dob else None,
                'location': u.permanent_location,
                'has_disability': u.has_disability,
                'disability_details': u.disability_details,
                'emergency_contact': u.emergency_contact,
                'is_active': u.is_active,
                'created_at': u.created_at.isoformat() if u.created_at else None,
                'updated_at': u.updated_at.isoformat() if u.updated_at else None,
            })

        return {'message': 'Users retrieved successfully', 'total_users': len(result), 'users': result}

    except Exception as e:
        raise AdminError(f'Failed to fetch users: {str(e)}', 500) from e


def get_payment_detail_admin(payment_id):
    try:
        log = PaymentLog.query.get(payment_id)
        if not log:
            raise AdminError('Payment record not found', 404)

        booking = Booking.query.get(log.booking_id) if log.booking_id else None
        route = Route.query.get(booking.route_id) if booking else None
        user_info = None
        if booking and booking.user:
            user_info = {
                'id': booking.user.id,
                'name': booking.user.name,
                'email': booking.user.email,
                'phone': booking.user.phone
            }

        return {
            'id': log.id,
            'checkout_session_id': log.checkout_session_id,
            'payment_intent_id': log.payment_intent_id,
            'booking_id': log.booking_id,
            'user': user_info,
            'route': {
                'source': route.source,
                'destination': route.destination
            } if route else None,
            'amount': f"INR {log.amount:.2f}",
            'amount_value': log.amount,
            'currency': log.currency,
            'status': log.status,
            'description': log.description,
            'payment_method': log.payment_method,
            'booking_status': booking.status if booking else None,
            'response_data': log.response_data,
            'created_at': log.created_at.isoformat() if log.created_at else None,
            'updated_at': log.updated_at.isoformat() if log.updated_at else None
        }

    except AdminError:
        raise
    except Exception as e:
        raise AdminError(f'Failed to fetch payment detail: {str(e)}', 500) from e


def get_all_drivers_admin():
    try:
        drivers = User.query.filter_by(role='driver').order_by(User.created_at.desc()).all()
        result = []
        for d in drivers:
            assigned_route = Route.query.filter_by(driver_id=d.id, status='active').first()
            bus_info = None
            if assigned_route and assigned_route.bus:
                bus_info = {
                    'bus_id': assigned_route.bus.id,
                    'bus_number': assigned_route.bus.bus_number,
                    'bus_type': assigned_route.bus.bus_type,
                    'total_seats': assigned_route.bus.total_seats,
                }
            result.append({
                'id': d.id,
                'name': d.name,
                'email': d.email,
                'phone': d.phone,
                'gender': d.gender,
                'dob': d.dob.isoformat() if d.dob else None,
                'location': d.permanent_location,
                'license_number': d.license_number,
                'experience_years': d.experience_years,
                'is_active': d.is_active,
                'assigned_bus': bus_info,
                'assigned_route': {
                    'id': assigned_route.id,
                    'source': assigned_route.source,
                    'destination': assigned_route.destination,
                } if assigned_route else None,
                'created_at': d.created_at.isoformat() if d.created_at else None,
            })
        return {'message': 'Drivers retrieved successfully', 'total_drivers': len(result), 'drivers': result}

    except Exception as e:
        raise AdminError(f'Failed to fetch drivers: {str(e)}', 500) from e


def update_driver_admin(driver_id, data):
    try:
        driver = User.query.get(driver_id)
        if not driver or driver.role != 'driver':
            raise AdminError('Driver not found', 404)

        if 'name' in data:
            driver.name = data['name']
        if 'email' in data:
            driver.email = data['email']
        if 'phone' in data:
            if not validate_indian_phone(data['phone']):
                raise AdminError('Invalid phone number', 400)
            driver.phone = format_indian_phone(data['phone'])
        if 'license_number' in data:
            driver.license_number = data['license_number']
        if 'experience_years' in data:
            driver.experience_years = int(data['experience_years'])

        driver.updated_at = datetime.utcnow()
        db.session.commit()

        return {'message': 'Driver updated successfully', 'driver_id': driver.id}

    except AdminError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Failed to update driver: {str(e)}', 500) from e


def get_all_agencies_admin():
    try:
        agencies = User.query.filter_by(role='bus_agency').order_by(User.created_at.desc()).all()
        result = []
        for a in agencies:
            profile = AgencyProfile.query.filter_by(user_id=a.id).first()
            booking_count = Booking.query.filter_by(user_id=a.id).count()
            result.append({
                'id': a.id,
                'name': a.name,
                'email': a.email,
                'phone': a.phone,
                'agency_name': profile.agency_name if profile else a.name,
                'address': profile.address if profile else None,
                'gst_number': profile.gst_number if profile else None,
                'contact_email': profile.contact_email if profile else None,
                'contact_phone': profile.contact_phone if profile else None,
                'total_bookings': booking_count,
                'is_active': a.is_active,
                'created_at': a.created_at.isoformat() if a.created_at else None,
            })
        return {'message': 'Agencies retrieved successfully', 'total_agencies': len(result), 'agencies': result}

    except Exception as e:
        raise AdminError(f'Failed to fetch agencies: {str(e)}', 500) from e


def register_agency_admin(data):
    try:
        name = data.get('name')
        email = data.get('email')
        password = data.get('password')
        phone_raw = data.get('phone')
        gender = data.get('gender')
        dob_raw = data.get('dob')

        if not name:
            raise AdminError('Name is required', 400)
        if not password:
            raise AdminError('Password is required', 400)
        if not phone_raw:
            raise AdminError('Phone number is required', 400)
        if not validate_indian_phone(phone_raw):
            raise AdminError('Invalid Indian phone number', 400)
        if not gender:
            raise AdminError('Gender is required', 400)
        if not dob_raw:
            raise AdminError('Date of birth is required', 400)

        phone = format_indian_phone(phone_raw)
        if User.query.filter_by(phone=phone).first():
            raise AdminError('Phone number already registered', 409)

        if email and User.query.filter_by(email=email).first():
            raise AdminError('Email already registered', 409)

        from ..core.extensions import bcrypt
        from uuid import uuid4
        if not email:
            email = f'agency_{uuid4().hex[:12]}@auto.local'

        dob = datetime.strptime(dob_raw.strip(), '%Y-%m-%d').date()
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')

        user = User(
            name=name, email=email, password=hashed, role='bus_agency',
            phone=phone, gender=gender.lower(), dob=dob,
            permanent_location=data.get('location'),
        )
        db.session.add(user)
        db.session.flush()

        profile = AgencyProfile(
            user_id=user.id,
            agency_name=data.get('agency_name', name),
            address=data.get('address'),
            gst_number=data.get('gst_number'),
            contact_email=data.get('contact_email', email),
            contact_phone=data.get('contact_phone', phone),
        )
        db.session.add(profile)
        db.session.commit()

        return {'message': 'Agency registered successfully', 'agency_id': user.id}

    except AdminError:
        db.session.rollback()
        raise
    except IntegrityError:
        db.session.rollback()
        raise AdminError('Database integrity error', 409)
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Agency registration failed: {str(e)}', 500) from e


def get_bus_occupancy(bus_id):
    try:
        bus = Bus.query.get(bus_id)
        if not bus:
            raise AdminError('Bus not found', 404)

        routes = Route.query.filter_by(bus_id=bus_id).all()
        occupancy_data = []

        for route in routes:
            total_booked = Booking.query.filter_by(route_id=route.id, status='booked').count()
            seat_statuses = SeatStatus.query.filter_by(route_id=route.id).all()
            seats = [{
                'seat_number': s.seat_number,
                'is_booked': s.is_booked,
                'booked_by': s.booked_by,
                'seat_type': s.seat_type,
            } for s in seat_statuses]

            occupancy_data.append({
                'route_id': route.id,
                'source': route.source,
                'destination': route.destination,
                'departure_time': route.departure_time.isoformat(),
                'arrival_time': route.arrival_time.isoformat(),
                'total_seats': bus.total_seats,
                'booked_seats': total_booked,
                'available_seats': bus.total_seats - total_booked,
                'seats': seats,
            })

        return {
            'message': 'Bus occupancy retrieved successfully',
            'bus_id': bus.id,
            'bus_number': bus.bus_number,
            'bus_type': bus.bus_type,
            'total_seats': bus.total_seats,
            'routes': occupancy_data,
        }

    except AdminError:
        raise
    except Exception as e:
        raise AdminError(f'Failed to fetch bus occupancy: {str(e)}', 500) from e


def export_bus_document(bus_id, fmt):
    try:
        bus = Bus.query.get(bus_id)
        if not bus:
            raise AdminError('Bus not found', 404)

        routes = Route.query.filter_by(bus_id=bus_id).all()
        base64_id = secrets.token_urlsafe(16)

        if fmt == 'pdf':
            html = f"""<html><body>
            <h1>Bus Export: {bus.bus_number} ({bus.bus_type})</h1>
            <p>Total Seats: {bus.total_seats}</p>
            <table border="1" cellpadding="5"><tr><th>Route</th><th>Departure</th><th>Arrival</th><th>Booked</th><th>Available</th></tr>"""
            for r in routes:
                booked = Booking.query.filter_by(route_id=r.id, status='booked').count()
                avail = bus.total_seats - booked
                html += f"<tr><td>{r.source}→{r.destination}</td><td>{r.departure_time.isoformat()}</td><td>{r.arrival_time.isoformat()}</td><td>{booked}</td><td>{avail}</td></tr>"
            html += "</table></body></html>"

            from xhtml2pdf import pisa
            pdf_buffer = io.BytesIO()
            pisa.CreatePDF(io.StringIO(html), dest=pdf_buffer)
            pdf_bytes = pdf_buffer.getvalue()

        elif fmt == 'csv':
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Route', 'Source', 'Destination', 'Departure', 'Arrival', 'Booked', 'Available'])
            for r in routes:
                booked = Booking.query.filter_by(route_id=r.id, status='booked').count()
                avail = bus.total_seats - booked
                writer.writerow([r.id, r.source, r.destination, r.departure_time.isoformat(), r.arrival_time.isoformat(), booked, avail])
            pdf_bytes = output.getvalue().encode('utf-8')

        elif fmt == 'xlsx':
            try:
                import openpyxl
            except ImportError:
                raise AdminError('openpyxl is required for XLSX export. Install with: pip install openpyxl', 400)

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Bus {bus.bus_number}"
            ws.append(['Route ID', 'Source', 'Destination', 'Departure', 'Arrival', 'Booked Seats', 'Available Seats'])
            for r in routes:
                booked = Booking.query.filter_by(route_id=r.id, status='booked').count()
                avail = bus.total_seats - booked
                ws.append([r.id, r.source, r.destination, r.departure_time.isoformat(), r.arrival_time.isoformat(), booked, avail])

            xlsx_buffer = io.BytesIO()
            wb.save(xlsx_buffer)
            pdf_bytes = xlsx_buffer.getvalue()
        else:
            raise AdminError(f'Unsupported format: {fmt}. Use pdf, csv, or xlsx', 400)

        doc = BusExportDocument(
            bus_id=bus_id,
            format=fmt,
            pdf_blob=pdf_bytes,
            base64_id=base64_id,
        )
        db.session.add(doc)
        db.session.commit()

        return {
            'message': f'Bus data exported as {fmt.upper()}',
            'bus_id': bus_id,
            'base64_id': base64_id,
            'format': fmt,
            'size_bytes': len(pdf_bytes),
            'download_url': f'/api/admin/buses/{bus_id}/export/{fmt}/download/{base64_id}',
        }

    except AdminError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise AdminError(f'Export failed: {str(e)}', 500) from e


def get_agency_tickets_admin(agency_id):
    try:
        agency = User.query.get(agency_id)
        if not agency or agency.role != 'bus_agency':
            raise AdminError('Agency not found', 404)

        bookings = Booking.query.filter_by(user_id=agency_id).order_by(Booking.booking_date.desc()).all()
        result = []
        for b in bookings:
            route = Route.query.get(b.route_id)
            passenger_details = [{
                'seat_number': pd.seat_number,
                'name': pd.name,
                'age': pd.age,
                'gender': pd.gender,
                'phone': pd.phone,
                'boarding_location': pd.boarding_location,
            } for pd in b.passenger_details]

            result.append({
                'booking_id': b.id,
                'route': {
                    'source': route.source if route else None,
                    'destination': route.destination if route else None,
                    'departure_time': route.departure_time.isoformat() if route else None,
                    'bus_number': route.bus.bus_number if route and route.bus else None,
                },
                'seat_numbers': b.seat_numbers,
                'total_amount': f"INR {b.total_amount:.2f}",
                'status': b.status,
                'payment_status': b.payment_status,
                'booking_date': b.booking_date.isoformat(),
                'passengers': passenger_details,
            })

        return {'message': 'Agency tickets retrieved successfully', 'total_tickets': len(result), 'tickets': result}

    except AdminError:
        raise
    except Exception as e:
        raise AdminError(f'Failed to fetch agency tickets: {str(e)}', 500) from e
