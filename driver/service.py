from datetime import datetime

from ..core.extensions import db
from ..core.models import Route


class DriverError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def get_assigned_routes(current_user):
    try:
        routes = Route.query.filter_by(driver_id=current_user.id).order_by(Route.departure_time).all()
        result = []
        for route in routes:
            result.append({
                'id': route.id,
                'source': route.source,
                'destination': route.destination,
                'departure_time': route.departure_time.isoformat(),
                'arrival_time': route.arrival_time.isoformat(),
                'bus_number': route.bus.bus_number if route.bus else None,
                'bus_type': route.bus.bus_type if route.bus else None,
                'price': route.price,
                'available_seats': route.available_seats,
                'status': route.status,
            })
        return {'message': 'Routes retrieved successfully', 'routes': result}

    except Exception as e:
        raise DriverError(f'Failed to fetch assigned routes: {str(e)}', 500) from e


def update_route_status(current_user, route_id, data):
    try:
        route = Route.query.get(route_id)
        if not route:
            raise DriverError('Route not found', 404)

        if route.driver_id != current_user.id:
            raise DriverError('Unauthorized: this route is not assigned to you', 403)

        new_status = data.get('status')
        if not new_status:
            raise DriverError('status is required', 400)

        valid_statuses = ['active', 'cancelled', 'completed']
        if new_status not in valid_statuses:
            raise DriverError(f'Invalid status. Must be one of: {", ".join(valid_statuses)}', 400)

        route.status = new_status
        route.updated_at = datetime.utcnow()

        if new_status == 'cancelled':
            route.cancellation_reason = data.get('cancellation_reason', 'Cancelled by driver')

        db.session.commit()

        return {
            'message': 'Route status updated successfully',
            'route_id': route.id,
            'status': route.status,
        }

    except DriverError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise DriverError(f'Failed to update route status: {str(e)}', 500) from e
