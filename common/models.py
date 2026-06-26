from datetime import datetime

from ..common.extensions import db


class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), default='user')
    phone = db.Column(db.String(20), nullable=False, unique=True)
    gender = db.Column(db.String(20), nullable=False)
    dob = db.Column(db.Date)
    permanent_location = db.Column(db.Text)
    has_disability = db.Column(db.Boolean, default=False)
    disability_details = db.Column(db.Text)
    emergency_contact = db.Column(db.String(20))
    profile_picture = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    bookings = db.relationship('Booking', backref='user', lazy=True)


class Bus(db.Model):
    __tablename__ = 'bus'
    id = db.Column(db.Integer, primary_key=True)
    bus_number = db.Column(db.String(50), unique=True, nullable=False)
    total_seats = db.Column(db.Integer, default=40)
    amenities = db.Column(db.String(200))
    bus_type = db.Column(db.String(50), default='Standard')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Route(db.Model):
    __tablename__ = 'route'
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    departure_time = db.Column(db.DateTime, nullable=False)
    arrival_time = db.Column(db.DateTime, nullable=False)
    bus_id = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    price = db.Column(db.Float, nullable=False)
    available_seats = db.Column(db.Integer, default=40)
    status = db.Column(db.String(50), default='active')
    cancellation_reason = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    bus = db.relationship('Bus', backref='routes')
    driver = db.relationship('User', backref='assigned_routes', foreign_keys=[driver_id])


class Booking(db.Model):
    __tablename__ = 'booking'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    route_id = db.Column(db.Integer, db.ForeignKey('route.id'), nullable=False)
    seat_numbers = db.Column(db.String(200), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    booking_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='booked')
    payment_intent_id = db.Column(db.String(200))
    checkout_session_id = db.Column(db.String(200))
    payment_status = db.Column(db.String(50), default='pending')
    transaction_id = db.Column(db.String(200))
    passenger_name = db.Column(db.String(200))
    passenger_email = db.Column(db.String(200))
    passenger_phone = db.Column(db.String(20))
    special_requests = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    route = db.relationship('Route', backref='bookings')


class SeatStatus(db.Model):
    __tablename__ = 'seat_status'
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('route.id'), nullable=False)
    seat_number = db.Column(db.Integer, nullable=False)
    is_booked = db.Column(db.Boolean, default=False)
    booked_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=True)
    seat_type = db.Column(db.String(50), default='regular')
    price_multiplier = db.Column(db.Float, default=1.0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    route = db.relationship('Route', backref='seat_statuses')
    booking = db.relationship('Booking', backref='seats')


class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    method = db.Column(db.String(10), nullable=False)
    path = db.Column(db.String(500), nullable=False)
    query_string = db.Column(db.Text)
    request_body = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    ip_address = db.Column(db.String(50))
    response_status = db.Column(db.Integer, nullable=False)
    response_body = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='audit_logs')


class PaymentLog(db.Model):
    __tablename__ = 'payment_log'
    id = db.Column(db.Integer, primary_key=True)
    payment_intent_id = db.Column(db.String(200))
    checkout_session_id = db.Column(db.String(200))
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'))
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='inr')
    status = db.Column(db.String(50))
    payment_method = db.Column(db.String(50))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    response_data = db.Column(db.Text)
    booking = db.relationship('Booking', backref='payment_logs')
