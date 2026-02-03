"""
VFS Booking Bot - Automation Package
"""
from .browser import BrowserManager
from .login import LoginAutomation
from .turnstile import TurnstileSolver
from .booking import BookingAutomation
from .monitor import SlotMonitor

__all__ = [
    "BrowserManager",
    "LoginAutomation",
    "TurnstileSolver",
    "BookingAutomation",
    "SlotMonitor",
]
