from datetime import date, datetime

from flask_bcrypt import Bcrypt

from ..common.extensions import bcrypt, db
from ..common.models import Booking, Bus, Route, SeatStatus, User


def seed_data_if_empty(app):
    with app.app_context():
        if Bus.query.count() > 0:
            return

        bus1 = Bus(bus_number='TN01AB1234', total_seats=40, amenities='AC, WiFi, Water, Charging Point', bus_type='AC Sleeper')
        bus2 = Bus(bus_number='TN02CD5678', total_seats=30, amenities='AC, TV, Water', bus_type='AC Seater')
        bus3 = Bus(bus_number='TN03EF9012', total_seats=50, amenities='AC, WiFi, Snacks, Entertainment', bus_type='Luxury')

        db.session.add_all([bus1, bus2, bus3])
        db.session.commit()

        routes_data = [
            Route(source='Chennai', destination='Bangalore', departure_time=datetime(2026, 6, 20, 6, 0), arrival_time=datetime(2026, 6, 20, 11, 0), bus_id=bus1.id, price=450.00, available_seats=40),
            Route(source='Chennai', destination='Bangalore', departure_time=datetime(2026, 6, 20, 22, 0), arrival_time=datetime(2026, 6, 21, 3, 0), bus_id=bus3.id, price=550.00, available_seats=50),
            Route(source='Madurai', destination='Trichy', departure_time=datetime(2026, 6, 20, 7, 0), arrival_time=datetime(2026, 6, 20, 9, 30), bus_id=bus2.id, price=250.00, available_seats=30),
            Route(source='Madurai', destination='Chennai', departure_time=datetime(2026, 6, 20, 8, 0), arrival_time=datetime(2026, 6, 20, 14, 0), bus_id=bus1.id, price=600.00, available_seats=40),
            Route(source='Bangalore', destination='Chennai', departure_time=datetime(2026, 6, 20, 7, 0), arrival_time=datetime(2026, 6, 20, 12, 0), bus_id=bus3.id, price=450.00, available_seats=50),
            Route(source='Trichy', destination='Madurai', departure_time=datetime(2026, 6, 20, 16, 0), arrival_time=datetime(2026, 6, 20, 18, 30), bus_id=bus2.id, price=250.00, available_seats=30),
            Route(source='Chennai', destination='Coimbatore', departure_time=datetime(2026, 6, 20, 21, 0), arrival_time=datetime(2026, 6, 21, 4, 0), bus_id=bus1.id, price=800.00, available_seats=40),
            Route(source='Coimbatore', destination='Chennai', departure_time=datetime(2026, 6, 20, 22, 0), arrival_time=datetime(2026, 6, 21, 5, 0), bus_id=bus1.id, price=800.00, available_seats=40),
        ]
        db.session.add_all(routes_data)

        admin_password = bcrypt.generate_password_hash('admin123').decode('utf-8')
        admin = User(name='Admin User', email='admin@example.com', password=admin_password, role='admin', phone='+919876543210', gender='male', dob=date(1990, 1, 1), permanent_location='Chennai', emergency_contact='+919876543200')
        db.session.add(admin)

        user_password = bcrypt.generate_password_hash('user123').decode('utf-8')
        user = User(name='Regular User', email='user@example.com', password=user_password, role='user', phone='+919876543211', gender='male', dob=date(1995, 5, 15), permanent_location='Bangalore', emergency_contact='+919876543201')
        db.session.add(user)

        db.session.commit()
