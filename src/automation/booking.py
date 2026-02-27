"""
Booking Automation - Handles VFS Global complete booking flow

Full flow (from screenshots analysis):
  Phase 5: Appointment Details (center, category, subcategory, payment mode, slot check)
  Phase 6: Your Details (fill form, 28s wait, save, reminder modal, fee notice)
  Phase 7: Identity Verification (face liveness + passport - delegated to identity_verification.py)
  Phase 8: Verification status polling + Booking OTP
  Phase 9: Book Appointment (Turnstile #3, appointment type, calendar, time slot)
  Phase 10: Review & Payment (T&C checkboxes, Multicaixa payment)
  Phase 11: Confirmation (parse reference number)
"""
import asyncio
import re
from datetime import date, datetime
from typing import Optional, Tuple, List, Dict, Callable
from urllib.parse import urlparse, parse_qs
from playwright.async_api import Page
from loguru import logger

from ..app.config import VFSUrls, Selectors
from .browser import BrowserManager


class BookingAutomation:
    """Handles VFS Global booking automation - complete flow from dashboard to confirmation"""

    def __init__(self, browser: BrowserManager):
        self.browser = browser
        self._on_verification_needed: Optional[Callable] = None
        self._otp_callback: Optional[Callable] = None

    def set_verification_callback(self, callback: Callable):
        """Set callback for when identity verification needs human intervention"""
        self._on_verification_needed = callback

    def set_otp_callback(self, callback: Callable):
        """Set callback for booking OTP retrieval"""
        self._otp_callback = callback

    # ================================================================
    # Phase 5: Appointment Details
    # ================================================================

    async def start_new_booking(self) -> Tuple[bool, str]:
        """Navigate to start new booking from dashboard"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Starting new booking...")

            # Navigate to dashboard if not there
            if "/dashboard" not in page.url:
                await page.goto(VFSUrls.DASHBOARD, wait_until="domcontentloaded", timeout=30000)
                await self.browser.random_delay(3000, 5000)

            # Wait for Angular to fully render dashboard content
            try:
                await page.wait_for_function(
                    """() => {
                        const body = document.body?.innerText || '';
                        return body.includes('Start New Booking') ||
                               body.includes('Active application') ||
                               body.includes('No Application');
                    }""",
                    timeout=15000,
                )
            except:
                logger.warning("Dashboard content didn't render in time, proceeding anyway...")

            await self._dismiss_cookie_consent(page)
            await self._wait_for_loading(page)
            await self.browser.screenshot("dashboard_before_booking")

            # Try multiple click strategies until URL leaves /dashboard
            click_strategies = [
                self._click_start_booking_via_selector,
                self._click_start_booking_via_js,
            ]

            for strategy in click_strategies:
                try:
                    clicked = await strategy(page)
                    if not clicked:
                        continue

                    # Wait for navigation away from dashboard
                    try:
                        await page.wait_for_function(
                            """() => !window.location.href.includes('/dashboard')""",
                            timeout=10000,
                        )
                    except:
                        pass

                    await self.browser.random_delay(1000, 2000)

                    # Verify we actually left the dashboard
                    if "/dashboard" not in page.url:
                        await self._wait_for_loading(page)
                        await self._dismiss_cookie_consent(page)
                        logger.info(f"New booking started (URL: {page.url})")
                        await self.browser.screenshot("booking_page_loaded")
                        return True, "New booking started"

                    logger.warning(f"Click didn't navigate (still on {page.url}), trying next strategy...")
                except Exception as e:
                    logger.warning(f"Click strategy failed: {e}")

            await self.browser.screenshot("no_booking_button")
            return False, "Start New Booking button not found or click didn't navigate"

        except Exception as e:
            logger.error(f"Failed to start booking: {e}")
            await self.browser.screenshot("start_booking_error")
            return False, f"Failed to start booking: {str(e)}"

    async def _click_start_booking_via_selector(self, page: Page) -> bool:
        """Try to click Start New Booking using Playwright selectors.

        VFS dashboard has TWO "Start New Booking" buttons:
        1. Mobile: <button class="... d-lg-none ...">  (hidden on desktop via d-lg-none)
        2. Desktop: <button class="... d-none d-lg-inline-block ...">  (hidden on mobile)
        Both are <button mat-raised-button class="btn btn-brand-orange ...">
        Text is inside: <span class="mdc-button__label"> Start New Booking </span>
        """
        selectors = [
            # Desktop button (d-lg-inline-block) — visible at >= 992px width
            "button.btn-brand-orange.d-lg-inline-block",
            # Mobile button (d-lg-none) — visible at < 992px width
            "button.btn-brand-orange.d-lg-none",
            # Any btn-brand-orange button (in case classes change)
            "button.btn-brand-orange",
            # Fallback: Angular Material button with text
            "button.mat-mdc-raised-button:has-text('Start New Booking')",
        ]

        for selector in selectors:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    logger.info(f"Clicking Start New Booking via: {selector}")
                    await el.click()
                    return True
            except:
                continue

        return False

    async def _click_start_booking_via_js(self, page: Page) -> bool:
        """Find and click Start New Booking via JavaScript.

        Finds the visible btn-brand-orange button and dispatches a click event.
        This bypasses any Playwright selector issues with Angular Material buttons.
        """
        result = await page.evaluate("""
            () => {
                // Strategy 1: Find by class (most reliable based on VFS HTML)
                const brandButtons = document.querySelectorAll('button.btn-brand-orange');
                for (const btn of brandButtons) {
                    const style = window.getComputedStyle(btn);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {
                        btn.click();
                        return {
                            found: true, strategy: 'btn-brand-orange',
                            tag: btn.tagName, classes: btn.className.substring(0, 80),
                            display: style.display,
                        };
                    }
                }

                // Strategy 2: Find by mdc-button__label text
                const labels = document.querySelectorAll('.mdc-button__label');
                for (const label of labels) {
                    if (label.textContent.trim() === 'Start New Booking') {
                        const btn = label.closest('button');
                        if (btn) {
                            btn.click();
                            return {
                                found: true, strategy: 'mdc-button__label',
                                tag: btn.tagName, classes: btn.className.substring(0, 80),
                            };
                        }
                    }
                }

                // Strategy 3: Brute force - any element with the text
                const allButtons = document.querySelectorAll('button, a, [role="button"]');
                for (const el of allButtons) {
                    if (el.innerText && el.innerText.trim().includes('Start New Booking')) {
                        el.click();
                        return {
                            found: true, strategy: 'text-search',
                            tag: el.tagName, classes: el.className.substring(0, 80),
                        };
                    }
                }

                return { found: false };
            }
        """)

        if result.get("found"):
            logger.info(f"JS click ({result.get('strategy')}): <{result.get('tag')}> class='{result.get('classes', '')}' display={result.get('display', 'n/a')}")
            return True

        return False

    async def select_center(self, center: str = "Luanda") -> Tuple[bool, str]:
        """Select visa application center"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info(f"Selecting center: {center}")

            await page.wait_for_selector(Selectors.CENTER_SELECT, timeout=15000)
            await self.browser.random_delay(500, 1000)

            # Check if already pre-selected
            current_value = await page.text_content(Selectors.CENTER_SELECT)
            if current_value and center.lower() in current_value.lower():
                logger.info(f"Center already selected: {current_value.strip()}")
                return True, f"Center already selected: {current_value.strip()}"

            await self.browser.human_click(Selectors.CENTER_SELECT)
            await self.browser.random_delay(500, 1000)

            option_selector = f"mat-option:has-text('{center}')"
            await page.wait_for_selector(option_selector, timeout=5000)
            await self.browser.human_click(option_selector)
            await self.browser.random_delay(1000, 2000)

            await self._wait_for_loading(page)
            logger.info(f"Center selected: {center}")
            return True, f"Center selected: {center}"

        except Exception as e:
            logger.error(f"Failed to select center: {e}")
            await self.browser.screenshot("select_center_error")
            return False, f"Failed to select center: {str(e)}"

    async def select_category(
        self,
        category: str = "Visto Schengen",
        subcategory: str = "Visto Schengen (Schengen Visa)"
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

            await self._wait_for_loading(page)

            logger.info(f"Category selected: {category} / {subcategory}")
            return True, f"Category selected: {category} / {subcategory}"

        except Exception as e:
            logger.error(f"Failed to select category: {e}")
            await self.browser.screenshot("select_category_error")
            return False, f"Failed to select category: {str(e)}"

    async def select_payment_mode(self, mode: str = "Multicaixa") -> Tuple[bool, str]:
        """Select payment mode dropdown"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info(f"Selecting payment mode: {mode}")

            # Wait for payment mode dropdown to appear
            try:
                await page.wait_for_selector(Selectors.PAYMENT_MODE_SELECT, timeout=10000)
            except:
                logger.info("Payment mode dropdown not found, may not be required")
                return True, "Payment mode not required"

            await self.browser.random_delay(500, 1000)
            await self.browser.human_click(Selectors.PAYMENT_MODE_SELECT)
            await self.browser.random_delay(500, 1000)

            # Select Multicaixa option
            option_selector = f"mat-option:has-text('{mode}')"
            await page.wait_for_selector(option_selector, timeout=5000)
            await self.browser.human_click(option_selector)
            await self.browser.random_delay(1000, 2000)

            await self._wait_for_loading(page)
            logger.info(f"Payment mode selected: {mode}")
            return True, f"Payment mode selected: {mode}"

        except Exception as e:
            logger.error(f"Failed to select payment mode: {e}")
            await self.browser.screenshot("select_payment_error")
            return False, f"Failed to select payment mode: {str(e)}"

    async def check_slot_availability(self) -> Tuple[bool, str, Optional[str]]:
        """Check if appointment slots are available

        Returns:
            (available, message, earliest_date_text)
        """
        page = self.browser.page
        if not page:
            return False, "Browser not started", None

        try:
            logger.info("Checking slot availability...")

            # Wait for availability result (AJAX after subcategory selection)
            await asyncio.sleep(3)
            await self._wait_for_loading(page)

            body_text = await page.text_content("body")
            body_lower = body_text.lower() if body_text else ""

            # Check for "no slots" message
            if "currently available" in body_lower and "sorry" in body_lower:
                logger.info("No slots available")
                await self.browser.screenshot("no_slots")
                return False, "No appointment slots are currently available", None

            if "no appointment" in body_lower:
                logger.info("No slots available")
                await self.browser.screenshot("no_slots")
                return False, "No appointment slots available", None

            # Check for "Earliest available slot" text
            if "earliest available slot" in body_lower:
                # Parse the date info
                match = re.search(
                    r"earliest available slot.*?is[:\s]*(\d{2}-\d{2}-\d{4})",
                    body_text, re.IGNORECASE
                )
                earliest = match.group(1) if match else "date found"
                logger.info(f"Slots available! Earliest: {earliest}")
                await self.browser.screenshot("slots_available")
                return True, f"Slots available. Earliest: {earliest}", earliest

            # Check for Continue button (indicates slots are available)
            continue_btn = await page.query_selector(Selectors.CONTINUE_BUTTON)
            if continue_btn and await continue_btn.is_visible():
                logger.info("Slots available (Continue button visible)")
                return True, "Slots available", None

            logger.info("No slot availability info found")
            await self.browser.screenshot("slot_check_unclear")
            return False, "No slot availability information found", None

        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            return False, f"Error checking availability: {str(e)}", None

    async def click_continue_after_slots(self) -> Tuple[bool, str]:
        """Click Continue button after slot availability is confirmed"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Clicking Continue after slot availability...")

            # Find and click Continue button
            await page.wait_for_selector(Selectors.CONTINUE_BUTTON, timeout=10000)
            await self.browser.random_delay(500, 1000)
            await self.browser.human_click(Selectors.CONTINUE_BUTTON)
            await self.browser.random_delay(2000, 3000)

            # Wait for navigation to /your-details
            try:
                await page.wait_for_function(
                    "() => window.location.href.includes('/your-details')",
                    timeout=15000,
                )
            except:
                pass

            await self._wait_for_loading(page)
            logger.info(f"Continued to: {page.url}")
            return True, "Navigated to Your Details"

        except Exception as e:
            logger.error(f"Failed to click Continue: {e}")
            await self.browser.screenshot("continue_error")
            return False, f"Failed to click Continue: {str(e)}"

    # ================================================================
    # Phase 6: Your Details
    # ================================================================

    async def fill_applicant_details(self, applicant: Dict) -> Tuple[bool, str]:
        """Fill applicant details form with 28-second wait"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Filling applicant details...")

            # Wait for the 28-second countdown
            logger.info("Waiting 30 seconds (mandatory wait before saving)...")
            await asyncio.sleep(30)

            # Wait for form
            await page.wait_for_selector(Selectors.FIRST_NAME, timeout=10000)

            # Fill text fields
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

            # Handle phone country code
            if applicant.get("dial_code"):
                await self._select_dropdown(Selectors.PHONE_CODE, applicant["dial_code"])

            await self.browser.screenshot("details_filled")
            logger.info("Applicant details filled")
            return True, "Applicant details filled"

        except Exception as e:
            logger.error(f"Failed to fill applicant details: {e}")
            await self.browser.screenshot("fill_details_error")
            return False, f"Failed to fill applicant details: {str(e)}"

    async def save_applicant_details(self) -> Tuple[bool, str]:
        """Click Save button after filling applicant form"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Saving applicant details...")

            await page.wait_for_selector(Selectors.SAVE_BUTTON, timeout=10000)
            await self.browser.random_delay(500, 1000)
            await self.browser.human_click(Selectors.SAVE_BUTTON)
            await self.browser.random_delay(2000, 3000)

            await self._wait_for_loading(page)
            await self.browser.screenshot("details_saved")
            logger.info("Applicant details saved")
            return True, "Details saved"

        except Exception as e:
            logger.error(f"Failed to save details: {e}")
            await self.browser.screenshot("save_error")
            return False, f"Failed to save details: {str(e)}"

    async def handle_reminder_modal(self) -> Tuple[bool, str]:
        """Handle 'Please keep your passport handy' reminder modal"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Checking for Reminder modal...")

            # Wait for modal to appear
            await asyncio.sleep(2)

            # Look for modal with reminder text
            body_text = await page.text_content("body")
            if body_text and "passport handy" in body_text.lower():
                logger.info("Reminder modal detected")

                # Click Continue in the modal
                continue_selectors = [
                    "mat-dialog-container button:has-text('Continue')",
                    ".modal button:has-text('Continue')",
                    ".cdk-overlay-container button:has-text('Continue')",
                    "button:has-text('Continue')",
                ]

                for selector in continue_selectors:
                    try:
                        btn = await page.query_selector(selector)
                        if btn and await btn.is_visible():
                            await btn.click()
                            logger.info("Reminder modal dismissed")
                            await self.browser.random_delay(1000, 2000)
                            await self._wait_for_loading(page)
                            return True, "Reminder modal handled"
                    except:
                        continue

            logger.info("No reminder modal found")
            return True, "No reminder modal"

        except Exception as e:
            logger.error(f"Reminder modal error: {e}")
            return True, f"Reminder modal handling: {str(e)}"

    async def handle_service_fee_notice(self) -> Tuple[bool, str]:
        """Handle service fee notice (AOA 40,700.00) and click Continue"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Checking for service fee notice...")
            await asyncio.sleep(2)

            body_text = await page.text_content("body")
            if body_text and ("service fee" in body_text.lower() or "40,700" in body_text):
                logger.info("Service fee notice detected (AOA 40,700.00)")
                await self.browser.screenshot("service_fee_notice")

                # Click Continue (not Go Back)
                continue_btn = await page.query_selector(Selectors.CONTINUE_BUTTON)
                if continue_btn and await continue_btn.is_visible():
                    await continue_btn.click()
                    logger.info("Service fee Continue clicked")
                    await self.browser.random_delay(2000, 3000)
                    await self._wait_for_loading(page)
                    return True, "Service fee accepted"

            logger.info("No service fee notice found")
            return True, "No service fee notice"

        except Exception as e:
            logger.error(f"Service fee notice error: {e}")
            return True, f"Service fee handling: {str(e)}"

    # ================================================================
    # Phase 7: Identity Verification (delegated)
    # ================================================================

    async def handle_identity_verification(self) -> Tuple[bool, str]:
        """Handle identity verification - detect and wait for completion

        Identity verification happens on idnvui.vfsglobal.com and requires
        a camera (face liveness + passport scan). The bot can either:
        1. Use pre-recorded video via Chrome fake video flags
        2. Pause and wait for human to complete manually
        """
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Checking for identity verification...")
            await asyncio.sleep(3)

            # Check if redirected to identity verification domain
            if "idnvui.vfsglobal.com" in page.url:
                logger.info("Identity verification page detected!")
                await self.browser.screenshot("identity_verification_start")

                # Notify via callback (Telegram)
                if self._on_verification_needed:
                    try:
                        if asyncio.iscoroutinefunction(self._on_verification_needed):
                            await self._on_verification_needed("verification_needed",
                                "Identity verification required. Please complete face + passport verification on the computer.")
                        else:
                            self._on_verification_needed("verification_needed",
                                "Identity verification required. Please complete face + passport verification on the computer.")
                    except Exception as e:
                        logger.error(f"Verification callback error: {e}")

                # Try to click Continue on the start page
                try:
                    continue_btn = await page.query_selector("button:has-text('CONTINUE'), button:has-text('Continue')")
                    if continue_btn:
                        await continue_btn.click()
                        logger.info("Clicked Continue on verification start page")
                        await self.browser.random_delay(2000, 3000)
                except:
                    pass

                # Wait for verification to complete (redirect back to VFS)
                # Human completes face + passport verification manually
                # Or fake video completes automatically
                logger.info("Waiting for identity verification to complete (up to 5 minutes)...")
                try:
                    await page.wait_for_function(
                        """() => {
                            // Check for completion message
                            const text = document.body?.innerText?.toLowerCase() || '';
                            if (text.includes('security check completed')) return true;
                            // Check if redirected back to VFS
                            if (window.location.href.includes('visa.vfsglobal.com')) return true;
                            return false;
                        }""",
                        timeout=300000,  # 5 minutes
                    )
                except:
                    logger.warning("Identity verification timeout (5 min)")
                    await self.browser.screenshot("verification_timeout")
                    return False, "Identity verification timeout"

                # If still on idnvui.vfsglobal.com, click Continue to redirect back
                if "idnvui.vfsglobal.com" in page.url:
                    try:
                        continue_btn = await page.query_selector("button:has-text('CONTINUE'), button:has-text('Continue')")
                        if continue_btn:
                            await continue_btn.click()
                            await self.browser.random_delay(3000, 5000)
                    except:
                        pass

                await self.browser.screenshot("verification_complete")
                logger.info("Identity verification completed")
                return True, "Identity verification completed"

            else:
                logger.info("No identity verification redirect detected")
                return True, "No identity verification needed"

        except Exception as e:
            logger.error(f"Identity verification error: {e}")
            await self.browser.screenshot("verification_error")
            return False, f"Identity verification error: {str(e)}"

    # ================================================================
    # Phase 8: Verification Status + Booking OTP
    # ================================================================

    async def wait_for_verification_passed(self) -> Tuple[bool, str]:
        """Wait for identity verification status to change to 'Verification Passed'"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Waiting for verification status...")

            # Poll for "Verification Passed" text (was "Verification pending")
            try:
                await page.wait_for_function(
                    """() => {
                        const text = document.body?.innerText || '';
                        return text.includes('Verification Passed') ||
                               text.includes('verification passed');
                    }""",
                    timeout=120000,  # 2 minutes
                )
                logger.info("Verification Passed!")
                await self.browser.screenshot("verification_passed")
                return True, "Verification passed"
            except:
                # Check current status
                body_text = await page.text_content("body")
                if body_text and "verification passed" in body_text.lower():
                    return True, "Verification passed"

                logger.warning("Verification status timeout")
                await self.browser.screenshot("verification_status_timeout")
                return False, "Verification status timeout"

        except Exception as e:
            logger.error(f"Verification status error: {e}")
            return False, f"Verification status error: {str(e)}"

    async def handle_booking_otp(self) -> Tuple[bool, str]:
        """Handle booking OTP (separate from login OTP)

        Flow: Click "Generate OTP" → Enter OTP (3-min validity, 5 attempts) → Verify → Continue
        """
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Handling booking OTP...")

            # Check if OTP section is visible
            body_text = await page.text_content("body")
            if not body_text or "one-time password" not in body_text.lower():
                logger.info("No booking OTP required")
                return True, "No booking OTP needed"

            # Click "Generate OTP"
            generate_btn = await page.query_selector(Selectors.GENERATE_OTP_BUTTON)
            if generate_btn:
                await generate_btn.click()
                logger.info("Generate OTP clicked")
                await self.browser.random_delay(2000, 3000)
            else:
                logger.warning("Generate OTP button not found")
                return False, "Generate OTP button not found"

            # Wait for OTP email to arrive
            logger.info("Waiting 10s for booking OTP email...")
            await asyncio.sleep(10)

            # Try to read OTP from email
            otp_code = await self._read_otp_from_email()

            # Use callback if configured
            if not otp_code and self._otp_callback:
                try:
                    if asyncio.iscoroutinefunction(self._otp_callback):
                        otp_code = await self._otp_callback()
                    else:
                        otp_code = self._otp_callback()
                except Exception as e:
                    logger.error(f"OTP callback error: {e}")

            # Retry email read
            if not otp_code:
                for retry in range(1, 6):
                    logger.info(f"OTP not found, retrying ({retry}/5)...")
                    await asyncio.sleep(10)
                    otp_code = await self._read_otp_from_email()
                    if otp_code:
                        break

            if not otp_code:
                logger.warning("Waiting for manual booking OTP entry (120s)...")
                success = await self._wait_for_manual_otp_entry(page, timeout=120)
                if success:
                    return True, "Booking OTP entered manually"
                return False, "Booking OTP timeout"

            # Enter the OTP code
            logger.info("Entering booking OTP...")
            otp_input = await page.query_selector(Selectors.BOOKING_OTP_INPUT)
            if otp_input:
                await otp_input.click()
                await self.browser.random_delay(200, 500)
                await page.keyboard.press("Control+A")
                await page.keyboard.type(otp_code, delay=80)
                await self.browser.random_delay(500, 1000)
            else:
                return False, "Booking OTP input not found"

            # Click Verify
            verify_btn = await page.query_selector(Selectors.VERIFY_OTP_BUTTON)
            if verify_btn:
                await verify_btn.click()
                logger.info("Verify OTP clicked")
                await self.browser.random_delay(2000, 3000)

            # Wait for success message
            await asyncio.sleep(2)
            body_text = await page.text_content("body")
            if body_text and "successfully verified" in body_text.lower():
                logger.info("Booking OTP verified successfully")
                await self.browser.screenshot("booking_otp_verified")

                # Click Continue
                continue_btn = await page.query_selector(Selectors.CONTINUE_BUTTON)
                if continue_btn and await continue_btn.is_visible():
                    await continue_btn.click()
                    await self.browser.random_delay(2000, 3000)

                return True, "Booking OTP verified"
            else:
                logger.warning("OTP verification unclear")
                await self.browser.screenshot("booking_otp_unclear")
                return False, "OTP verification failed"

        except Exception as e:
            logger.error(f"Booking OTP error: {e}")
            await self.browser.screenshot("booking_otp_error")
            return False, f"Booking OTP error: {str(e)}"

    # ================================================================
    # Phase 9: Book Appointment (Calendar + Time Slot)
    # ================================================================

    async def solve_booking_turnstile(self) -> Tuple[bool, str]:
        """Solve Turnstile #3 on the book-appointment page (modal popup)"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Checking for Turnstile on booking page...")
            await asyncio.sleep(2)

            # Check for captcha modal
            body_text = await page.text_content("body")
            has_captcha = body_text and ("verify captcha" in body_text.lower() or "verificando" in body_text.lower())

            # Check for Turnstile iframe
            turnstile = await page.query_selector(Selectors.TURNSTILE_IFRAME)

            if has_captcha or turnstile:
                logger.info("Turnstile #3 detected on booking page")

                # Import TurnstileSolver
                from .turnstile import TurnstileSolver
                solver = TurnstileSolver()

                # First check if it auto-solves
                await self.browser.random_delay(3000, 5000)
                success_check = await page.evaluate("""
                    () => {
                        const text = document.body.innerText.toLowerCase();
                        return text.includes('success') && !text.includes('verify captcha');
                    }
                """)

                if success_check:
                    logger.info("Turnstile #3 auto-solved")
                else:
                    token = await solver.solve(page)
                    if not token:
                        logger.warning("Failed to solve Turnstile #3")
                        return False, "Turnstile #3 solving failed"
                    logger.info("Turnstile #3 solved via 2Captcha")

                # Click Submit button in captcha modal
                submit_selectors = [
                    "button:has-text('Submit')",
                    ".modal button:has-text('Submit')",
                    ".cdk-overlay-container button:has-text('Submit')",
                ]
                for selector in submit_selectors:
                    try:
                        btn = await page.query_selector(selector)
                        if btn and await btn.is_visible():
                            await btn.click()
                            logger.info("Captcha Submit clicked")
                            await self.browser.random_delay(2000, 3000)
                            break
                    except:
                        continue

                await self._wait_for_loading(page)
                return True, "Turnstile #3 solved"

            logger.info("No Turnstile on booking page")
            return True, "No Turnstile needed"

        except Exception as e:
            logger.error(f"Booking Turnstile error: {e}")
            return False, f"Booking Turnstile error: {str(e)}"

    async def select_appointment_type(self) -> Tuple[bool, str]:
        """Select appointment type dropdown ('Choose a slot')"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Selecting appointment type...")
            await asyncio.sleep(2)

            # Try to find and click the appointment type dropdown
            dropdown = await page.query_selector(Selectors.APPOINTMENT_TYPE_SELECT)
            if dropdown and await dropdown.is_visible():
                await self.browser.human_click(Selectors.APPOINTMENT_TYPE_SELECT)
                await self.browser.random_delay(500, 1000)

                # Select first available option
                option = await page.query_selector("mat-option")
                if option:
                    await option.click()
                    await self.browser.random_delay(1000, 2000)
                    await self._wait_for_loading(page)

            logger.info("Appointment type handled")
            return True, "Appointment type selected"

        except Exception as e:
            logger.error(f"Appointment type error: {e}")
            return True, f"Appointment type: {str(e)}"

    async def select_slot(self, target_date: Optional[date] = None) -> Tuple[bool, str]:
        """Select appointment date from calendar and time slot"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Selecting appointment slot...")

            # Wait for calendar to appear
            await asyncio.sleep(3)
            await self._wait_for_loading(page)

            # Find available date cells in calendar
            available_selectors = [
                "td:not(.disabled):not(.unavailable):not([class*='disabled'])",
                ".date-available",
                "td.available",
                "td[class*='available']",
            ]

            date_found = False
            for selector in available_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    clickable = []
                    for el in elements:
                        text = await el.text_content()
                        if text and text.strip().isdigit():
                            clickable.append(el)

                    if clickable:
                        if target_date:
                            # Try to find specific date
                            for el in clickable:
                                text = await el.text_content()
                                if text and text.strip() == str(target_date.day):
                                    await el.click()
                                    date_found = True
                                    logger.info(f"Selected target date: {target_date.day}")
                                    break
                        if not date_found and clickable:
                            # Click first available date
                            await clickable[0].click()
                            text = await clickable[0].text_content()
                            date_found = True
                            logger.info(f"Selected first available date: {text.strip() if text else 'unknown'}")
                        if date_found:
                            break
                except:
                    continue

            if not date_found:
                await self.browser.screenshot("no_available_dates")
                return False, "No available dates found in calendar"

            await self.browser.random_delay(1000, 2000)
            await self._wait_for_loading(page)

            # Select time slot - look for "Select" buttons in the time slot table
            logger.info("Looking for time slots...")
            await asyncio.sleep(2)

            select_btn = await page.query_selector(Selectors.TIME_SLOT_SELECT)
            if select_btn:
                await select_btn.click()
                logger.info("Time slot selected")
                await self.browser.random_delay(1000, 2000)
            else:
                # Try Load More first, then select
                load_more = await page.query_selector(Selectors.LOAD_MORE)
                if load_more:
                    await load_more.click()
                    await self.browser.random_delay(1000, 2000)

                select_btn = await page.query_selector(Selectors.TIME_SLOT_SELECT)
                if select_btn:
                    await select_btn.click()
                    logger.info("Time slot selected after Load More")
                    await self.browser.random_delay(1000, 2000)
                else:
                    await self.browser.screenshot("no_time_slots")
                    return False, "No time slots found"

            # Click Continue after slot selection
            await asyncio.sleep(1)
            continue_btn = await page.query_selector(Selectors.CONTINUE_BUTTON)
            if continue_btn and await continue_btn.is_visible():
                await continue_btn.click()
                await self.browser.random_delay(2000, 3000)
                await self._wait_for_loading(page)

            await self.browser.screenshot("slot_selected")
            logger.info("Slot selected successfully")
            return True, "Slot selected"

        except Exception as e:
            logger.error(f"Failed to select slot: {e}")
            await self.browser.screenshot("select_slot_error")
            return False, f"Failed to select slot: {str(e)}"

    # ================================================================
    # Phase 10: Review & Payment
    # ================================================================

    async def handle_review_and_payment(self) -> Tuple[bool, str]:
        """Handle review page: check T&C boxes and click payment button"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Handling review & payment...")

            # Wait for review page
            await asyncio.sleep(2)
            await self._wait_for_loading(page)
            await self.browser.screenshot("review_page")

            # Check first T&C checkbox (Terms and Conditions)
            try:
                checkbox1 = await page.query_selector(Selectors.TERMS_CHECKBOX_1)
                if checkbox1:
                    await checkbox1.click()
                    logger.info("Terms & Conditions checkbox checked")
                    await self.browser.random_delay(500, 1000)
            except Exception as e:
                logger.warning(f"T&C checkbox 1: {e}")

            # Check second checkbox (communication consent)
            try:
                checkbox2 = await page.query_selector(Selectors.TERMS_CHECKBOX_2)
                if checkbox2:
                    # Make sure it's a different element than checkbox1
                    await checkbox2.click()
                    logger.info("Communication consent checkbox checked")
                    await self.browser.random_delay(500, 1000)
            except Exception as e:
                logger.warning(f"T&C checkbox 2: {e}")

            await self.browser.screenshot("checkboxes_checked")

            # Click payment button (Multicaixa Express Application/Bank/ATM)
            payment_selectors = [
                "button:has-text('Multicaixa')",
                Selectors.PAYMENT_BUTTON,
                "button:has-text('Pay')",
                "button:has-text('Confirm')",
                "button:has-text('Submit')",
            ]

            for selector in payment_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn and await btn.is_visible():
                        await self.browser.random_delay(500, 1000)
                        await btn.click()
                        logger.info(f"Payment button clicked: {selector}")
                        await self.browser.random_delay(3000, 5000)
                        await self._wait_for_loading(page)
                        break
                except:
                    continue

            await self.browser.screenshot("payment_submitted")
            logger.info("Review & payment completed")
            return True, "Payment submitted"

        except Exception as e:
            logger.error(f"Review/payment error: {e}")
            await self.browser.screenshot("review_payment_error")
            return False, f"Review/payment error: {str(e)}"

    # ================================================================
    # Phase 11: Confirmation
    # ================================================================

    async def get_confirmation(self) -> Tuple[bool, str, Optional[Dict]]:
        """Parse confirmation page and extract booking details"""
        page = self.browser.page
        if not page:
            return False, "Browser not started", None

        try:
            logger.info("Getting booking confirmation...")

            await asyncio.sleep(3)
            await self._wait_for_loading(page)
            await self.browser.screenshot("confirmation_page")

            confirmation = {}

            # Parse URL parameters
            parsed_url = urlparse(page.url)
            params = parse_qs(parsed_url.query)
            if params.get("RequestRefNo"):
                confirmation["reference_number"] = params["RequestRefNo"][0]
            if params.get("TransactionId"):
                confirmation["transaction_id"] = params["TransactionId"][0]
            if params.get("PaymentStatus"):
                confirmation["payment_status"] = params["PaymentStatus"][0]

            # Parse page body for additional info
            body_text = await page.text_content("body")
            if body_text:
                # Extract appointment reference (XYZ...)
                ref_match = re.search(r'(XYZ\d+)', body_text)
                if ref_match:
                    confirmation["appointment_ref"] = ref_match.group(1)

                # Extract amount
                amount_match = re.search(r'AOA\s*([\d,]+\.?\d*)', body_text)
                if amount_match:
                    confirmation["amount"] = amount_match.group(1)

                # Check for success message
                if "thank you" in body_text.lower() and "booking" in body_text.lower():
                    confirmation["status"] = "success"

                # Extract entity code
                entity_match = re.search(r'entity\s*code[:\s]*(\d+)', body_text, re.IGNORECASE)
                if entity_match:
                    confirmation["entity_code"] = entity_match.group(1)

                # Extract bank reference
                bank_ref_match = re.search(r'bank\s*reference[:\s]*(\d+)', body_text, re.IGNORECASE)
                if bank_ref_match:
                    confirmation["bank_reference"] = bank_ref_match.group(1)

            if confirmation:
                logger.info(f"Booking confirmed: {confirmation}")
                return True, "Booking confirmed", confirmation
            else:
                # Check for error
                error = await self._get_error_message(page)
                if error:
                    return False, f"Booking failed: {error}", None

                logger.warning("Confirmation details unclear")
                await self.browser.screenshot("confirmation_unclear")
                return False, "Confirmation details unclear", None

        except Exception as e:
            logger.error(f"Confirmation error: {e}")
            await self.browser.screenshot("confirmation_error")
            return False, f"Confirmation error: {str(e)}", None

    # ================================================================
    # Full Booking Flow (all phases)
    # ================================================================

    async def execute_full_booking(
        self,
        applicant: Dict,
        center: str = "Luanda",
        category: str = "Visto Schengen",
        subcategory: str = "Visto Schengen (Schengen Visa)",
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Execute complete booking flow from dashboard to confirmation

        Full flow:
        1. Start new booking (dashboard → /application-detail)
        2. Select center
        3. Select category + subcategory
        4. Select payment mode
        5. Check slot availability
        6. Click Continue
        7. Fill applicant details (with 28s wait)
        8. Save details
        9. Handle reminder modal
        10. Handle service fee notice
        11. Identity verification (face + passport)
        12. Wait for verification passed
        13. Handle booking OTP
        14. Solve Turnstile #3
        15. Select appointment type
        16. Select date + time slot
        17. Handle review & payment (T&C + Multicaixa)
        18. Get confirmation
        """
        name = f"{applicant.get('first_name', '')} {applicant.get('last_name', '')}"
        logger.info(f"===== Starting full booking for {name} =====")

        # Phase 5: Appointment Details
        success, message = await self.start_new_booking()
        if not success:
            return False, message, None

        success, message = await self.select_center(center)
        if not success:
            return False, message, None

        success, message = await self.select_category(category, subcategory)
        if not success:
            return False, message, None

        success, message = await self.select_payment_mode()
        if not success:
            return False, message, None

        available, message, earliest = await self.check_slot_availability()
        if not available:
            return False, message, None

        success, message = await self.click_continue_after_slots()
        if not success:
            return False, message, None

        # Phase 6: Your Details
        success, message = await self.fill_applicant_details(applicant)
        if not success:
            return False, message, None

        success, message = await self.save_applicant_details()
        if not success:
            return False, message, None

        success, message = await self.handle_reminder_modal()
        if not success:
            return False, message, None

        success, message = await self.handle_service_fee_notice()
        if not success:
            return False, message, None

        # Phase 7: Identity Verification
        success, message = await self.handle_identity_verification()
        if not success:
            return False, message, None

        # Phase 8: Verification Status + Booking OTP
        success, message = await self.wait_for_verification_passed()
        if not success:
            return False, message, None

        # Click Continue after verification passed
        page = self.browser.page
        if page:
            continue_btn = await page.query_selector(Selectors.CONTINUE_BUTTON)
            if continue_btn and await continue_btn.is_visible():
                await continue_btn.click()
                await self.browser.random_delay(2000, 3000)

        success, message = await self.handle_booking_otp()
        if not success:
            return False, message, None

        # Phase 9: Book Appointment
        success, message = await self.solve_booking_turnstile()
        if not success:
            return False, message, None

        success, message = await self.select_appointment_type()
        if not success:
            return False, message, None

        success, message = await self.select_slot()
        if not success:
            return False, message, None

        # Phase 10: Review & Payment
        success, message = await self.handle_review_and_payment()
        if not success:
            return False, message, None

        # Phase 11: Confirmation
        return await self.get_confirmation()

    # ================================================================
    # Helper Methods
    # ================================================================

    async def _dismiss_cookie_consent(self, page: Page):
        """Dismiss cookie consent banner if present"""
        try:
            reject_btn = await page.query_selector(Selectors.COOKIE_REJECT)
            if reject_btn:
                await reject_btn.click(timeout=5000)
                logger.info("Cookie consent dismissed (reject)")
                await self.browser.random_delay(500, 1000)
                return

            for selector in [
                "#onetrust-accept-btn-handler",
                "button:has-text('Accept')",
                "button:has-text('Reject')",
                ".onetrust-close-btn-handler",
            ]:
                try:
                    btn = await page.query_selector(selector)
                    if btn:
                        await btn.click(timeout=5000)
                        logger.info(f"Cookie consent dismissed via {selector}")
                        await self.browser.random_delay(500, 1000)
                        return
                except:
                    continue

            # JS fallback
            await page.evaluate("""
                () => {
                    const selectors = ['#onetrust-consent-sdk', '#onetrust-banner-sdk',
                                       '.onetrust-pc-dark-filter', '#onetrust-pc-sdk'];
                    selectors.forEach(s => {
                        const el = document.querySelector(s);
                        if (el) el.remove();
                    });
                    document.body.style.overflow = 'auto';
                }
            """)
        except Exception as e:
            logger.debug(f"Cookie consent dismiss: {e}")

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
        """Select value from Angular Material dropdown"""
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

    async def _get_error_message(self, page: Page) -> Optional[str]:
        """Extract error message from page"""
        try:
            for selector in [Selectors.ERROR_MESSAGE, ".alert-danger", ".error-message", ".mat-error"]:
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

    async def _read_otp_from_email(self) -> Optional[str]:
        """Read booking OTP from email via IMAP"""
        from ..app.config import settings

        if not settings.smtp_user or not settings.smtp_password:
            logger.info("Email not configured for booking OTP")
            return None

        try:
            import imaplib
            import email as email_lib
            from datetime import timedelta

            logger.info("Checking email for booking OTP...")

            imap = imaplib.IMAP4_SSL("imap.gmail.com")
            imap.login(settings.smtp_user, settings.smtp_password)
            imap.select("INBOX")

            date_str = (datetime.now() - timedelta(minutes=5)).strftime("%d-%b-%Y")

            message_ids_list = []
            for search_from in ["vfshelpline", "vfs"]:
                _, message_ids = imap.search(None, f'(SINCE "{date_str}" FROM "{search_from}")')
                if message_ids[0]:
                    message_ids_list = message_ids[0].split()
                    break

            if not message_ids_list:
                _, message_ids = imap.search(None, f'(SINCE "{date_str}" SUBJECT "OTP")')
                if message_ids[0]:
                    message_ids_list = message_ids[0].split()

            if not message_ids_list:
                imap.logout()
                return None

            latest_id = message_ids_list[-1]
            _, msg_data = imap.fetch(latest_id, "(RFC822)")
            msg = email_lib.message_from_bytes(msg_data[0][1])

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type in ("text/plain", "text/html"):
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        if content_type == "text/plain":
                            break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

            imap.logout()

            otp_match = re.search(r'\b(\d{6})\b', body)
            if otp_match:
                otp = otp_match.group(1)
                logger.info(f"Booking OTP found: {otp[:2]}****")
                return otp

            otp_match = re.search(r'\b(\d{4})\b', body)
            if otp_match:
                otp = otp_match.group(1)
                logger.info(f"Booking OTP found: {otp[:2]}**")
                return otp

            return None

        except Exception as e:
            logger.error(f"Failed to read booking OTP from email: {e}")
            return None

    async def _wait_for_manual_otp_entry(self, page: Page, timeout: int = 120) -> bool:
        """Wait for user to manually enter booking OTP"""
        try:
            await page.wait_for_function(
                """() => {
                    const text = document.body?.innerText?.toLowerCase() || '';
                    return text.includes('successfully verified') ||
                           text.includes('otp verification successful') ||
                           window.location.href.includes('/book-appointment');
                }""",
                timeout=timeout * 1000,
            )
            return True
        except:
            return False
