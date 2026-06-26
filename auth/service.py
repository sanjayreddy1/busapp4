from datetime import datetime
from uuid import uuid4

from flask import request
from sqlalchemy.exc import IntegrityError

from ..core.auth import format_indian_phone, generate_token, validate_indian_phone
from ..core.config import Config
from ..core.extensions import bcrypt, db
from ..core.models import User


class AuthError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def register_user(data):
    try:
        name = data.get('name') or request.args.get('name')
        email = data.get('email') or request.args.get('email')
        password = data.get('password') or request.args.get('password')
        phone_raw = data.get('phone') or request.args.get('phone')
        gender = data.get('gender') or request.args.get('gender')
        dob_raw = data.get('dob') or request.args.get('dob')

        if name:
            pass
        else:
            raise AuthError('Name is required', 400)

        if password:
            pass
        else:
            raise AuthError('Password is required', 400)

        if phone_raw:
            if not validate_indian_phone(phone_raw):
                raise AuthError('Invalid Indian phone number. Format: 9876543210 or +919876543210', 400)
        else:
            raise AuthError('Phone number is required', 400)

        phone = format_indian_phone(phone_raw)

        if User.query.filter_by(phone=phone).first():
            raise AuthError('Phone number already registered', 409)

        if gender:
            gender = gender.lower()
            if gender not in ('male', 'female', 'other'):
                raise AuthError('Gender must be male, female, or other', 400)
        else:
            raise AuthError('Gender is required', 400)

        if dob_raw:
            try:
                dob = datetime.strptime(dob_raw.strip(), '%Y-%m-%d').date()
            except (ValueError, AttributeError):
                raise AuthError('Invalid date of birth. Use YYYY-MM-DD format', 400)
        else:
            raise AuthError('Date of birth is required', 400)

        if email:
            if User.query.filter_by(email=email).first():
                raise AuthError('Email already registered', 409)
        else:
            email = f'user_{uuid4().hex[:12]}@auto.local'

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        has_disability = data.get('has_disability')
        if has_disability is not None:
            if isinstance(has_disability, bool):
                has_disability = has_disability
            elif isinstance(has_disability, str):
                has_disability = has_disability.lower() in ('true', '1', 'yes')
            else:
                has_disability = bool(has_disability)
        else:
            has_disability = False

        location = data.get('location') or data.get('permanent_location')
        emergency_contact_raw = data.get('emergency_contact')
        if emergency_contact_raw:
            if not validate_indian_phone(emergency_contact_raw):
                raise AuthError('Invalid Indian phone number for emergency contact', 400)
            emergency_contact = format_indian_phone(emergency_contact_raw)
        else:
            emergency_contact = None

        user = User(
            name=name,
            email=email,
            password=hashed_password,
            role='user',
            phone=phone,
            gender=gender,
            dob=dob,
            permanent_location=location,
            has_disability=has_disability,
            disability_details=data.get('disability_details') if has_disability else None,
            emergency_contact=emergency_contact,
            profile_picture=data.get('profile_picture')
        )

        db.session.add(user)
        db.session.commit()

        return {
            'message': 'User created successfully',
            'user_id': user.id,
            'phone': user.phone,
            'gender': user.gender,
            'email': user.email
        }

    except AuthError:
        db.session.rollback()
        raise
    except IntegrityError as e:
        db.session.rollback()
        raise AuthError('Database integrity error: user may already exist', 409) from e
    except Exception as e:
        db.session.rollback()
        raise AuthError(f'Registration failed: {str(e)}', 500) from e


def login_user(data):
    try:
        login_id = data.get('email') or data.get('phone') or request.args.get('email') or request.args.get('phone')
        password = data.get('password') or request.args.get('password')

        if login_id:
            pass
        else:
            raise AuthError('Email or phone number is required', 400)

        if password:
            pass
        else:
            raise AuthError('Password is required', 400)

        is_email = '@' in login_id
        if not is_email and not login_id.startswith('+'):
            try:
                formatted_phone = format_indian_phone(login_id)
            except Exception:
                formatted_phone = None
        else:
            formatted_phone = None

        if formatted_phone and formatted_phone != '+91':
            user = User.query.filter(
                (User.email == login_id) | (User.phone == formatted_phone) | (User.phone == login_id)
            ).first()
        else:
            user = User.query.filter(
                (User.email == login_id) | (User.phone == login_id)
            ).first()

        if not user or not bcrypt.check_password_hash(user.password, password):
            raise AuthError('Invalid credentials', 401)

        token = generate_token(user.id, user.role)

        return {
            'message': 'Login successful',
            'token': token,
            'user': {
                'id': user.id,
                'name': user.name,
                'email': user.email,
                'role': user.role,
                'phone': user.phone,
                'gender': user.gender,
                'dob': user.dob.isoformat() if user.dob else None,
                'location': user.permanent_location,
                'has_disability': user.has_disability,
                'disability_details': user.disability_details,
                'emergency_contact': user.emergency_contact,
                'profile_picture': user.profile_picture
            }
        }

    except AuthError:
        raise
    except Exception as e:
        raise AuthError(f'Login failed: {str(e)}', 500) from e


def register_admin(data):
    try:
        name = data.get('name') or request.args.get('name')
        email = data.get('email') or request.args.get('email')
        password = data.get('password') or request.args.get('password')
        phone_raw = data.get('phone') or request.args.get('phone')
        gender = data.get('gender') or request.args.get('gender')
        dob_raw = data.get('dob') or request.args.get('dob')

        if name:
            pass
        else:
            raise AuthError('Name is required', 400)

        if password:
            pass
        else:
            raise AuthError('Password is required', 400)

        if phone_raw:
            if not validate_indian_phone(phone_raw):
                raise AuthError('Invalid Indian phone number', 400)
        else:
            raise AuthError('Phone number is required', 400)

        phone = format_indian_phone(phone_raw)

        if User.query.filter_by(phone=phone).first():
            raise AuthError('Phone number already registered', 409)

        if gender:
            gender = gender.lower()
            if gender not in ('male', 'female', 'other'):
                raise AuthError('Gender must be male, female, or other', 400)
        else:
            raise AuthError('Gender is required', 400)

        if dob_raw:
            try:
                dob = datetime.strptime(dob_raw.strip(), '%Y-%m-%d').date()
            except (ValueError, AttributeError):
                raise AuthError('Invalid date of birth. Use YYYY-MM-DD format', 400)
        else:
            raise AuthError('Date of birth is required', 400)

        if email:
            if User.query.filter_by(email=email).first():
                raise AuthError('Email already registered', 409)
        else:
            email = f'admin_{uuid4().hex[:12]}@auto.local'

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        has_disability = data.get('has_disability')
        if has_disability is not None:
            if isinstance(has_disability, bool):
                has_disability = has_disability
            elif isinstance(has_disability, str):
                has_disability = has_disability.lower() in ('true', '1', 'yes')
            else:
                has_disability = bool(has_disability)
        else:
            has_disability = False

        location = data.get('location') or data.get('permanent_location')
        emergency_contact_raw = data.get('emergency_contact')
        if emergency_contact_raw:
            if not validate_indian_phone(emergency_contact_raw):
                raise AuthError('Invalid Indian phone number for emergency contact', 400)
            emergency_contact = format_indian_phone(emergency_contact_raw)
        else:
            emergency_contact = None

        admin = User(
            name=name,
            email=email,
            password=hashed_password,
            role='admin',
            phone=phone,
            gender=gender,
            dob=dob,
            permanent_location=location,
            has_disability=has_disability,
            disability_details=data.get('disability_details') if has_disability else None,
            emergency_contact=emergency_contact,
        )

        db.session.add(admin)
        db.session.commit()

        return {'message': 'Admin created successfully', 'admin_id': admin.id}

    except AuthError:
        db.session.rollback()
        raise
    except IntegrityError as e:
        db.session.rollback()
        raise AuthError('Database integrity error: admin may already exist', 409) from e
    except Exception as e:
        db.session.rollback()
        raise AuthError(f'Admin registration failed: {str(e)}', 500) from e


def _register_role(data, role):
    try:
        name = data.get('name') or (request.args.get('name') if request else None)
        email = data.get('email') or (request.args.get('email') if request else None)
        password = data.get('password') or (request.args.get('password') if request else None)
        phone_raw = data.get('phone') or (request.args.get('phone') if request else None)
        gender = data.get('gender') or (request.args.get('gender') if request else None)
        dob_raw = data.get('dob') or (request.args.get('dob') if request else None)

        if not name:
            raise AuthError('Name is required', 400)
        if not password:
            raise AuthError('Password is required', 400)
        if not phone_raw:
            raise AuthError('Phone number is required', 400)
        if not validate_indian_phone(phone_raw):
            raise AuthError('Invalid Indian phone number', 400)

        phone = format_indian_phone(phone_raw)
        if User.query.filter_by(phone=phone).first():
            raise AuthError('Phone number already registered', 409)

        if gender:
            gender = gender.lower()
            if gender not in ('male', 'female', 'other'):
                raise AuthError('Gender must be male, female, or other', 400)
        else:
            raise AuthError('Gender is required', 400)

        if dob_raw:
            try:
                dob = datetime.strptime(dob_raw.strip(), '%Y-%m-%d').date()
            except (ValueError, AttributeError):
                raise AuthError('Invalid date of birth. Use YYYY-MM-DD format', 400)
        else:
            raise AuthError('Date of birth is required', 400)

        if email:
            if User.query.filter_by(email=email).first():
                raise AuthError('Email already registered', 409)
        else:
            email = f'{role}_{uuid4().hex[:12]}@auto.local'

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        location = data.get('location') or data.get('permanent_location')
        emergency_contact_raw = data.get('emergency_contact')
        if emergency_contact_raw:
            if not validate_indian_phone(emergency_contact_raw):
                raise AuthError('Invalid Indian phone number for emergency contact', 400)
            emergency_contact = format_indian_phone(emergency_contact_raw)
        else:
            emergency_contact = None

        user = User(
            name=name, email=email, password=hashed_password, role=role,
            phone=phone, gender=gender, dob=dob,
            permanent_location=location, emergency_contact=emergency_contact,
        )
        db.session.add(user)
        db.session.commit()

        return {'message': f'{role} registered successfully', f'{role}_id': user.id}

    except AuthError:
        db.session.rollback()
        raise
    except IntegrityError as e:
        db.session.rollback()
        raise AuthError(f'Database integrity error', 409) from e
    except Exception as e:
        db.session.rollback()
        raise AuthError(f'{role} registration failed: {str(e)}', 500) from e


def register_driver(data):
    return _register_role(data, 'driver')


def register_agency(data):
    return _register_role(data, 'bus_agency')
