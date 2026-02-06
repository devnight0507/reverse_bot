"""
Slot Monitor - Continuously monitors for available appointment slots
"""
import asyncio
from datetime import datetime
from typing import Optional, Callable, List, Dict
from loguru import logger

from ..app.config import settings
from .browser import BrowserManager
from .login import LoginAutomation
from .booking import BookingAutomation


class SlotMonitor:
    """Monitors VFS Global for available appointment slots"""

    def __init__(
        self,
        browser: BrowserManager,
        on_slot_found: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ):
        self.browser = browser
        self.login = LoginAutomation(browser)
        self.booking = BookingAutomation(browser)
        self.on_slot_found = on_slot_found
        self.on_error = on_error

        self._running = False
        self._paused = False
        self._last_check = None
        self._check_count = 0
        self._slot_found_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def last_check(self) -> Optional[datetime]:
        return self._last_check

    @property
    def stats(self) -> Dict:
        return {
            "running": self._running,
            "paused": self._paused,
            "last_check": self._last_check,
            "check_count": self._check_count,
            "slot_found_count": self._slot_found_count,
        }

    async def start(
        self,
        applicants: List[Dict],
        center: str = "Luanda",
        category: str = "Short Stay",
        subcategory: str = "Tourism",
        interval: Optional[int] = None,
        auto_book: bool = True,
    ):
        """
        Start monitoring for slots

        Args:
            applicants: List of applicant data dictionaries
            center: Visa application center
            category: Visa category
            subcategory: Visa subcategory
            interval: Check interval in seconds (uses config if not provided)
            auto_book: Automatically book when slot is found
        """
        if self._running:
            logger.warning("Monitor is already running")
            return

        interval = interval or settings.monitor_interval
        self._running = True
        self._paused = False

        logger.info(f"Starting slot monitor (interval: {interval}s, auto_book: {auto_book})")
        logger.info(f"Monitoring for {len(applicants)} applicants")

        try:
            # Login first (don't check dashboard before login - causes Session Expired)
            logger.info("Logging in...")
            success, message = await self.login.login()
            if not success:
                logger.error(f"Login failed: {message}")
                if self.on_error:
                    await self._call_callback(self.on_error, "login_failed", message)
                self._running = False
                return

            # Start monitoring loop
            while self._running:
                if self._paused:
                    await asyncio.sleep(1)
                    continue

                try:
                    self._last_check = datetime.utcnow()
                    self._check_count += 1

                    logger.info(f"Check #{self._check_count} at {self._last_check}")

                    # Navigate to booking page
                    success, message = await self.booking.start_new_booking()
                    if not success:
                        logger.warning(f"Failed to start booking: {message}")
                        # Try to re-login
                        success, _ = await self.login.login()
                        if not success:
                            await asyncio.sleep(interval)
                            continue

                    # Select center and category
                    success, message = await self.booking.select_center(center)
                    if not success:
                        logger.warning(f"Failed to select center: {message}")
                        await asyncio.sleep(interval)
                        continue

                    success, message = await self.booking.select_category(category, subcategory)
                    if not success:
                        logger.warning(f"Failed to select category: {message}")
                        await asyncio.sleep(interval)
                        continue

                    # Check availability
                    available, message, dates = await self.booking.check_slot_availability()

                    if available and dates:
                        self._slot_found_count += 1
                        logger.info(f"SLOTS FOUND! {len(dates)} available dates")

                        # Notify callback
                        if self.on_slot_found:
                            await self._call_callback(
                                self.on_slot_found,
                                "slot_found",
                                {"dates": dates, "count": len(dates)}
                            )

                        if auto_book:
                            # Try to book for each applicant
                            for applicant in applicants:
                                if applicant.get("status") in ["booked", "cancelled"]:
                                    continue

                                logger.info(f"Attempting to book for {applicant.get('first_name')}")

                                # Fill details and book
                                success, msg = await self.booking.fill_applicant_details(applicant)
                                if not success:
                                    continue

                                success, msg = await self.booking.select_slot()
                                if not success:
                                    continue

                                success, msg, code = await self.booking.confirm_booking()
                                if success:
                                    logger.info(f"Booking successful for {applicant.get('first_name')}: {code}")
                                    applicant["status"] = "booked"
                                    applicant["confirmation_code"] = code

                                    if self.on_slot_found:
                                        await self._call_callback(
                                            self.on_slot_found,
                                            "booking_success",
                                            {"applicant": applicant, "code": code}
                                        )

                                    # Check if all applicants are booked
                                    pending = [a for a in applicants if a.get("status") not in ["booked", "cancelled"]]
                                    if not pending:
                                        logger.info("All applicants booked! Stopping monitor.")
                                        self._running = False
                                        break
                                else:
                                    logger.warning(f"Booking failed for {applicant.get('first_name')}: {msg}")
                    else:
                        logger.info(f"No slots available: {message}")

                except Exception as e:
                    logger.error(f"Monitor error: {e}")
                    if self.on_error:
                        await self._call_callback(self.on_error, "monitor_error", str(e))

                # Wait for next check
                if self._running:
                    logger.info(f"Next check in {interval} seconds...")
                    await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.info("Monitor cancelled")
        except Exception as e:
            logger.error(f"Monitor fatal error: {e}")
            if self.on_error:
                await self._call_callback(self.on_error, "fatal_error", str(e))
        finally:
            self._running = False
            logger.info("Monitor stopped")

    async def stop(self):
        """Stop monitoring"""
        logger.info("Stopping monitor...")
        self._running = False

    async def pause(self):
        """Pause monitoring"""
        logger.info("Pausing monitor...")
        self._paused = True

    async def resume(self):
        """Resume monitoring"""
        logger.info("Resuming monitor...")
        self._paused = False

    async def check_once(
        self,
        center: str = "Luanda",
        category: str = "Short Stay",
        subcategory: str = "Tourism",
    ) -> Dict:
        """
        Perform single slot check

        Returns:
            Dict with check results
        """
        result = {
            "available": False,
            "dates": [],
            "message": "",
            "checked_at": datetime.utcnow(),
        }

        try:
            # Ensure logged in
            logged_in = await self.login.check_session()
            if not logged_in:
                success, message = await self.login.login()
                if not success:
                    result["message"] = f"Login failed: {message}"
                    return result

            # Navigate to booking
            success, message = await self.booking.start_new_booking()
            if not success:
                result["message"] = message
                return result

            # Select center and category
            success, message = await self.booking.select_center(center)
            if not success:
                result["message"] = message
                return result

            success, message = await self.booking.select_category(category, subcategory)
            if not success:
                result["message"] = message
                return result

            # Check availability
            available, message, dates = await self.booking.check_slot_availability()
            result["available"] = available
            result["dates"] = [d.isoformat() for d in dates]
            result["message"] = message

            return result

        except Exception as e:
            result["message"] = f"Error: {str(e)}"
            return result

    async def _call_callback(self, callback: Callable, event: str, data):
        """Safely call callback function"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event, data)
            else:
                callback(event, data)
        except Exception as e:
            logger.error(f"Callback error: {e}")
