"""
Booking Automation - Handles VFS Global booking flow
"""
import asyncio
from datetime import date, datetime
from typing import Optional, Tuple, List, Dict
from playwright.async_api import Page
from loguru import logger

from ..app.config import VFSUrls, Selectors
from .browser import BrowserManager


class BookingAutomation:
    """Handles VFS Global booking automation"""

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    async def start_new_booking(self) -> Tuple[bool, str]:
        """Navigate to start new booking"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Starting new booking...")

            # Navigate to dashboard if not there
            if "/dashboard" not in page.url:
                await page.goto(VFSUrls.DASHBOARD, wait_until="networkidle")
                await self.browser.random_delay(1000, 2000)

            # Click "Start New Booking" button
            await page.wait_for_selector(Selectors.NEW_BOOKING_BUTTON, timeout=10000)
            await self.browser.human_click(Selectors.NEW_BOOKING_BUTTON)
            await self.browser.random_delay(1000, 2000)

            # Wait for booking page to load
            await self.browser.wait_for_navigation()

            logger.info("New booking started")
            return True, "New booking started"

        except Exception as e:
            logger.error(f"Failed to start booking: {e}")
            await self.browser.screenshot("start_booking_error")
            return False, f"Failed to start booking: {str(e)}"

    async def select_center(self, center: str = "Luanda") -> Tuple[bool, str]:
        """Select visa application center"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info(f"Selecting center: {center}")

            # Wait for center dropdown
            await page.wait_for_selector(Selectors.CENTER_SELECT, timeout=10000)
            await self.browser.random_delay(500, 1000)

            # Click dropdown to open
            await self.browser.human_click(Selectors.CENTER_SELECT)
            await self.browser.random_delay(500, 1000)

            # Select center option
            option_selector = f"mat-option:has-text('{center}')"
            await page.wait_for_selector(option_selector, timeout=5000)
            await self.browser.human_click(option_selector)
            await self.browser.random_delay(1000, 2000)

            # Wait for loading to complete
            await self._wait_for_loading(page)

            logger.info(f"Center selected: {center}")
            return True, f"Center selected: {center}"

        except Exception as e:
            logger.error(f"Failed to select center: {e}")
            await self.browser.screenshot("select_center_error")
            return False, f"Failed to select center: {str(e)}"

    async def select_category(
        self,
        category: str = "Short Stay",
        subcategory: str = "Tourism"
    ) -> Tuple[bool, str]:
        """Select visa category and subcategory"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info(f"Selecting category: {category} / {subcategory}")

            # Select main category
            await page.wait_for_selector(Selectors.CATEGORY_SELECT, timeout=10000)
            await self.browser.random_delay(500, 1000)

            await self.browser.human_click(Selectors.CATEGORY_SELECT)
            await self.browser.random_delay(500, 1000)

            category_option = f"mat-option:has-text('{category}')"
            await page.wait_for_selector(category_option, timeout=5000)
            await self.browser.human_click(category_option)
            await self.browser.random_delay(1000, 2000)

            # Wait for loading
            await self._wait_for_loading(page)

            # Select subcategory
            await page.wait_for_selector(Selectors.SUBCATEGORY_SELECT, timeout=10000)
            await self.browser.random_delay(500, 1000)

            await self.browser.human_click(Selectors.SUBCATEGORY_SELECT)
            await self.browser.random_delay(500, 1000)

            subcategory_option = f"mat-option:has-text('{subcategory}')"
            await page.wait_for_selector(subcategory_option, timeout=5000)
            await self.browser.human_click(subcategory_option)
            await self.browser.random_delay(1000, 2000)

            # Wait for slot availability check
            await self._wait_for_loading(page)

            logger.info(f"Category selected: {category} / {subcategory}")
            return True, f"Category selected: {category} / {subcategory}"

        except Exception as e:
            logger.error(f"Failed to select category: {e}")
            await self.browser.screenshot("select_category_error")
            return False, f"Failed to select category: {str(e)}"

    async def check_slot_availability(self) -> Tuple[bool, str, List[date]]:
        """Check if appointment slots are available"""
        page = self.browser.page
        if not page:
            return False, "Browser not started", []

        try:
            logger.info("Checking slot availability...")

            # Wait for availability message or slots
            await asyncio.sleep(2)  # Give time for AJAX response

            # Check for "no slots" message
            no_slots_element = await page.query_selector(Selectors.NO_SLOTS_MESSAGE)
            if no_slots_element:
                text = await no_slots_element.text_content()
                if "no appointment" in text.lower() or "not available" in text.lower():
                    logger.info("No slots available")
                    return False, "No appointment slots are currently available", []

            # Check for available dates
            available_dates = await self._get_available_dates(page)
            if available_dates:
                logger.info(f"Found {len(available_dates)} available dates")
                return True, f"Found {len(available_dates)} available dates", available_dates

            logger.info("No slots found")
            return False, "No slots found", []

        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            return False, f"Error checking availability: {str(e)}", []

    async def fill_applicant_details(self, applicant: Dict) -> Tuple[bool, str]:
        """Fill applicant details form"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Filling applicant details...")

            # Wait for form
            await page.wait_for_selector(Selectors.FIRST_NAME, timeout=10000)

            # Fill each field
            fields = [
                (Selectors.FIRST_NAME, applicant.get("first_name", "")),
                (Selectors.LAST_NAME, applicant.get("last_name", "")),
                (Selectors.PASSPORT_NUMBER, applicant.get("passport_number", "")),
                (Selectors.PHONE_NUMBER, applicant.get("phone", "")),
                (Selectors.EMAIL, applicant.get("email", "")),
                (Selectors.CONFIRM_EMAIL, applicant.get("email", "")),
            ]

            for selector, value in fields:
                if value:
                    try:
                        await self.browser.human_type(selector, value)
                        await self.browser.random_delay(200, 500)
                    except Exception as e:
                        logger.warning(f"Could not fill {selector}: {e}")

            # Handle date fields
            if applicant.get("date_of_birth"):
                dob = applicant["date_of_birth"]
                if isinstance(dob, date):
                    dob = dob.strftime("%d/%m/%Y")
                await self._fill_date_field(Selectors.DOB_INPUT, dob)

            if applicant.get("passport_expiry"):
                expiry = applicant["passport_expiry"]
                if isinstance(expiry, date):
                    expiry = expiry.strftime("%d/%m/%Y")
                await self._fill_date_field(Selectors.PASSPORT_EXPIRY, expiry)

            # Handle dropdowns
            if applicant.get("gender"):
                await self._select_dropdown(Selectors.GENDER_SELECT, applicant["gender"])

            if applicant.get("nationality"):
                await self._select_dropdown(Selectors.NATIONALITY_SELECT, applicant["nationality"])

            logger.info("Applicant details filled")
            return True, "Applicant details filled"

        except Exception as e:
            logger.error(f"Failed to fill applicant details: {e}")
            await self.browser.screenshot("fill_details_error")
            return False, f"Failed to fill applicant details: {str(e)}"

    async def select_slot(self, target_date: Optional[date] = None) -> Tuple[bool, str]:
        """Select appointment date and time slot"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Selecting appointment slot...")

            # Find available date
            if target_date:
                # Try to select specific date
                date_str = target_date.strftime("%Y-%m-%d")
                date_selector = f"[data-date='{date_str}'], .date-available:has-text('{target_date.day}')"
            else:
                # Select first available date
                date_selector = Selectors.AVAILABLE_DATE

            await page.wait_for_selector(date_selector, timeout=10000)
            await self.browser.human_click(date_selector)
            await self.browser.random_delay(1000, 2000)

            # Wait for time slots to load
            await self._wait_for_loading(page)

            # Select first available time slot
            await page.wait_for_selector(Selectors.TIME_SLOT, timeout=10000)
            await self.browser.human_click(Selectors.TIME_SLOT)
            await self.browser.random_delay(500, 1000)

            logger.info("Slot selected")
            return True, "Slot selected"

        except Exception as e:
            logger.error(f"Failed to select slot: {e}")
            await self.browser.screenshot("select_slot_error")
            return False, f"Failed to select slot: {str(e)}"

    async def confirm_booking(self) -> Tuple[bool, str, Optional[str]]:
        """Confirm the booking and get confirmation code"""
        page = self.browser.page
        if not page:
            return False, "Browser not started", None

        try:
            logger.info("Confirming booking...")

            # Accept terms and conditions
            terms_checkbox = await page.query_selector(Selectors.TERMS_CHECKBOX)
            if terms_checkbox:
                await self.browser.human_click(Selectors.TERMS_CHECKBOX)
                await self.browser.random_delay(500, 1000)

            # Click confirm button
            confirm_selectors = [
                Selectors.CONFIRM_BUTTON,
                Selectors.PAY_BUTTON,
                "button:has-text('Submit')",
                "button:has-text('Book')",
            ]

            for selector in confirm_selectors:
                try:
                    button = await page.query_selector(selector)
                    if button:
                        await self.browser.human_click(selector)
                        break
                except:
                    continue

            # Wait for confirmation
            await self.browser.random_delay(2000, 3000)
            await self._wait_for_loading(page)

            # Get confirmation code
            confirmation_code = await self._get_confirmation_code(page)

            if confirmation_code:
                logger.info(f"Booking confirmed! Code: {confirmation_code}")
                await self.browser.screenshot("booking_success")
                return True, "Booking confirmed", confirmation_code
            else:
                # Check for success message
                success = await page.query_selector(Selectors.SUCCESS_MESSAGE)
                if success:
                    logger.info("Booking confirmed (no code found)")
                    await self.browser.screenshot("booking_success")
                    return True, "Booking confirmed", None

                # Check for error
                error = await self._get_error_message(page)
                if error:
                    logger.error(f"Booking failed: {error}")
                    await self.browser.screenshot("booking_failed")
                    return False, f"Booking failed: {error}", None

                logger.warning("Booking status unclear")
                await self.browser.screenshot("booking_unclear")
                return False, "Booking status unclear", None

        except Exception as e:
            logger.error(f"Failed to confirm booking: {e}")
            await self.browser.screenshot("confirm_error")
            return False, f"Failed to confirm booking: {str(e)}", None

    async def execute_full_booking(
        self,
        applicant: Dict,
        center: str = "Luanda",
        category: str = "Short Stay",
        subcategory: str = "Tourism"
    ) -> Tuple[bool, str, Optional[str]]:
        """Execute complete booking flow"""
        logger.info(f"Starting full booking for {applicant.get('first_name')} {applicant.get('last_name')}")

        # Step 1: Start new booking
        success, message = await self.start_new_booking()
        if not success:
            return False, message, None

        # Step 2: Select center
        success, message = await self.select_center(center)
        if not success:
            return False, message, None

        # Step 3: Select category
        success, message = await self.select_category(category, subcategory)
        if not success:
            return False, message, None

        # Step 4: Check availability
        available, message, dates = await self.check_slot_availability()
        if not available:
            return False, message, None

        # Step 5: Fill applicant details
        success, message = await self.fill_applicant_details(applicant)
        if not success:
            return False, message, None

        # Step 6: Select slot
        success, message = await self.select_slot()
        if not success:
            return False, message, None

        # Step 7: Confirm booking
        return await self.confirm_booking()

    # ============== Helper Methods ==============

    async def _wait_for_loading(self, page: Page, timeout: int = 10000):
        """Wait for loading spinner to disappear"""
        try:
            spinner = await page.query_selector(Selectors.SPINNER)
            if spinner:
                await page.wait_for_selector(Selectors.SPINNER, state="hidden", timeout=timeout)
        except:
            pass

        try:
            loading = await page.query_selector(Selectors.LOADING)
            if loading:
                await page.wait_for_selector(Selectors.LOADING, state="hidden", timeout=timeout)
        except:
            pass

        await self.browser.random_delay(500, 1000)

    async def _get_available_dates(self, page: Page) -> List[date]:
        """Get list of available appointment dates"""
        dates = []
        try:
            elements = await page.query_selector_all(Selectors.AVAILABLE_DATE)
            for element in elements:
                try:
                    date_attr = await element.get_attribute("data-date")
                    if date_attr:
                        dates.append(datetime.strptime(date_attr, "%Y-%m-%d").date())
                except:
                    continue
        except:
            pass
        return dates

    async def _fill_date_field(self, selector: str, date_str: str):
        """Fill a date input field"""
        page = self.browser.page
        if not page:
            return

        try:
            element = await page.query_selector(selector)
            if element:
                await element.click()
                await self.browser.random_delay(200, 400)
                await element.fill(date_str)
                await self.browser.random_delay(200, 400)
        except Exception as e:
            logger.warning(f"Could not fill date field {selector}: {e}")

    async def _select_dropdown(self, selector: str, value: str):
        """Select value from dropdown"""
        page = self.browser.page
        if not page:
            return

        try:
            await self.browser.human_click(selector)
            await self.browser.random_delay(500, 1000)
            option_selector = f"mat-option:has-text('{value}')"
            await page.wait_for_selector(option_selector, timeout=5000)
            await self.browser.human_click(option_selector)
            await self.browser.random_delay(500, 1000)
        except Exception as e:
            logger.warning(f"Could not select {value} from {selector}: {e}")

    async def _get_confirmation_code(self, page: Page) -> Optional[str]:
        """Extract confirmation code from page"""
        try:
            # Look for confirmation code in various formats
            patterns = [
                r"confirmation[:\s]+([A-Z0-9]+)",
                r"reference[:\s]+([A-Z0-9]+)",
                r"booking[:\s]+([A-Z0-9]+)",
            ]

            text = await page.text_content("body")
            import re
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return match.group(1)

            return None
        except:
            return None

    async def _get_error_message(self, page: Page) -> Optional[str]:
        """Extract error message from page"""
        try:
            error_selectors = [
                Selectors.ERROR_MESSAGE,
                ".alert-danger",
                ".error-message",
                ".mat-error",
            ]

            for selector in error_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text = await element.text_content()
                        if text:
                            return text.strip()
                except:
                    continue

            return None
        except:
            return None
