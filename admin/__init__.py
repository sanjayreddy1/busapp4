"""Admin domain package."""

from .service import (  # noqa: F401
    AdminError,
    get_dashboard_stats,
    get_all_tickets,
    update_ticket_status,
    manage_routes_get,
    manage_routes_create,
    create_bus,
    patch_bus,
    patch_route,
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
