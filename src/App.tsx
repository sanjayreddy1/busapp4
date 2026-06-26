import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";

import Header from "./components/Header";
import Footer from "./components/Footer";
import AdminLayout from "./pages/adminPage/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Chatbot from "./components/Chatbot";

// Super Admin Pages
import SuperAdminDashboard from "./pages/adminPage/SuperAdmin/Dashboard";
import RoutesManagement from "./pages/adminPage/SuperAdmin/RoutesManagement";
import BusManagement from "./pages/adminPage/SuperAdmin/BusManagement";
import TicketsView from "./pages/adminPage/SuperAdmin/TicketsView";
import PaymentHistories from "./pages/adminPage/SuperAdmin/PaymentHistories";
import UserManagement from "./pages/adminPage/SuperAdmin/UserManagement";
import BookingsManagement from "./pages/adminPage/SuperAdmin/BookingsManagement";
import DriverManagement from "./pages/adminPage/SuperAdmin/DriverManagement";
import TicketValidation from "./pages/adminPage/SuperAdmin/TicketValidation";
import BusTrackingAdmin from "./pages/adminPage/SuperAdmin/BusTracking";
import Analytics from "./pages/adminPage/SuperAdmin/Analytics";

// Agent Pages
import AgentDashboard from "./pages/adminPage/Agent/Dashboard";
import BulkBooking from "./pages/adminPage/Agent/BulkBooking";
import BookingHistory from "./pages/adminPage/Agent/BookingHistory";
import AgentPaymentHistory from "./pages/adminPage/Agent/PaymentHistory";

// Pages
import { Index as LandingPage } from "./pages/LandingPage";
import Services from "./pages/LandingPage/Services";
import About from "./pages/LandingPage/About";
import Contact from "./pages/LandingPage/Contact";

import Index from "./pages/Index";
import Auth from "./pages/auth/Auth";
import SearchResults from "./pages/SearchResults";
import BookTicket from "./pages/BookTicket";
import SeatSelection from "./pages/SeatSelection";
import Dashboard from "./pages/ProfilePage/Profile";
import TicketView from "./pages/TicketView";
import BusTracking from "./pages/BusTracking";
import NotFound from "./pages/NotFound";
import { RoleProvider, UserRole } from "./hooks/RoleContext";
import Help from "./pages/HelpScreen";
import AllRoutesScreen from "./pages/AllRoutes";
import { AuthProvider } from "./hooks/AuthContext";
import React, { useEffect, useState } from "react";


const queryClient = new QueryClient();

const HeaderWrapper = () => {
  const location = useLocation();
  const isAuthPage = location.pathname.startsWith('/auth');
  return !isAuthPage ? <Header /> : null;
};

const FooterWrapper = () => {
  const location = useLocation();
  const isAuthPage = location.pathname.startsWith('/auth');
  const isAdmin = location.pathname.startsWith('/admin');
  return !isAuthPage && !isAdmin ? <Footer /> : null;
};

const LayoutWrapper = ({ children }: { children: React.ReactNode }) => {
  const location = useLocation();
  const isAuthPage = location.pathname.startsWith('/auth');
  return <div className={!isAuthPage ? "" : ""}>{children}</div>;
};

const App = () => {
  const [loading, setLoading] = useState(true);
  const [hasAccess, setHasAccess] = useState(false);
  useEffect(() => {
    const checkAuth = () => {
      const token = sessionStorage.getItem('auth_tokens');
      const user = sessionStorage.getItem('auth_user');
      // Basic check: authenticated if both exist
      const isTokenValid = !!token && !!user;
      let userRole: UserRole | null = null;
      try {
        if (user) {
          const parsed = JSON.parse(user);
          userRole = parsed?.role;
        }
      } catch (e) {
        userRole = null;
      }
      setHasAccess(!!isTokenValid && (userRole === 'admin' || userRole === 'agent'));
      setLoading(false);
    };

    checkAuth();

    const handleStorage = (e: StorageEvent) => {
      if (e.key === 'auth_user' || e.key === 'auth_tokens') {
        checkAuth();
      }
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

return (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <RoleProvider>
        <AuthProvider>
          <BrowserRouter>
            {/* Public Header */}
            <HeaderWrapper />

            <LayoutWrapper>
              <Routes>
                {/* PUBLIC ROUTES */}
                <Route path="/" element={<LandingPage />} />
                <Route path="/services" element={<Services />} />
                <Route path="/about" element={<About />} />
                <Route path="/contact" element={<Contact />} />
                <Route path="/dashboard" element={<Dashboard />} />

                <Route path="/booking" element={<Index />} />
                <Route path="/auth" element={<Auth />} />
                <Route path="/auth/:mode" element={<Auth />} />

                <Route path="/search" element={<SearchResults />} />
                <Route path="/book-ticket" element={<BookTicket />} />
                <Route path="/seat-selection/:busId" element={<SeatSelection />} />
                <Route path="/ticket/:ticketId" element={<TicketView />} />
                <Route path="/track-bus/:ticketId" element={<BusTracking />} />
                <Route path="/AllRoutes" element={<AllRoutesScreen />} />
                {/* <Route path="/help" element={<Help />} /> */}

                {/* ADMIN ROUTES (Sidebar Layout) */}
                <Route element={<ProtectedRoute allowedRoles={['admin', 'agent']} />}>
                  <Route path="/admin" element={<AdminLayout />}>
                    {/* Super Admin Routes */}
                    <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
                      <Route path="dashboard" element={<SuperAdminDashboard />} />
                      <Route path="analytics" element={<Analytics />} />
                      <Route path="routes" element={<RoutesManagement />} />
                      <Route path="buses" element={<BusManagement />} />
                      <Route path="tickets" element={<TicketsView />} />
                      <Route path="payments" element={<PaymentHistories />} />
                      <Route path="users" element={<UserManagement />} />
                      <Route path="bookings" element={<BookingsManagement />} />
                      <Route path="drivers" element={<DriverManagement />} />
                      <Route path="ticket-validation" element={<TicketValidation />} />
                      <Route path="bus-tracking" element={<BusTrackingAdmin />} />
                    </Route>

                    {/* Agent Routes */}
                    <Route element={<ProtectedRoute allowedRoles={['agent', 'admin']} />}>
                      <Route path="agent/dashboard" element={<AgentDashboard />} />
                      <Route path="agent/bulk-booking" element={<BulkBooking />} />
                      <Route path="agent/bookings" element={<BookingHistory />} />
                      <Route path="agent/payments" element={<AgentPaymentHistory />} />
                    </Route>
                  </Route>
                </Route>
                {/* NOT FOUND */}
                <Route path="*" element={<NotFound />} />
              </Routes>
            </LayoutWrapper>

            <FooterWrapper />
            <Chatbot />
          </BrowserRouter>
        </AuthProvider>
      </RoleProvider>
    </TooltipProvider>
  </QueryClientProvider>
)};

export default App;
