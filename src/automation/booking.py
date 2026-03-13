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
from pathlib import Path
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

                    # Wait for navigation OR form rendering
                    # Angular SPA may change URL or render form content without URL change
                    try:
                        await page.wait_for_function(
                            """() => {
                                // Check if URL changed away from dashboard
                                if (!window.location.href.includes('/dashboard')) return true;
                                // Check if appointment form rendered (Angular component loaded)
                                const body = document.body?.innerText || '';
                                if (body.includes('Appointment Details')) return true;
                                if (body.includes('Application Centre')) return true;
                                if (document.querySelector("mat-select[formcontrolname='centerCode']")) return true;
                                if (document.querySelector("app-eligibility-criteria")) return true;
                                return false;
                            }""",
                            timeout=15000,
                        )
                    except:
                        pass

                    await self.browser.random_delay(1000, 2000)

                    # Verify: either URL changed or booking form appeared
                    url_changed = "/dashboard" not in page.url
                    form_present = await page.evaluate("""
                        () => {
                            const body = document.body?.innerText || '';
                            return body.includes('Appointment Details') ||
                                   body.includes('Application Centre') ||
                                   !!document.querySelector("mat-select[formcontrolname='centerCode']") ||
                                   !!document.querySelector("app-eligibility-criteria");
                        }
                    """)

                    if url_changed or form_present:
                        await self._wait_for_loading(page)
                        await self._dismiss_cookie_consent(page)
                        logger.info(f"New booking started (URL: {page.url}, form_present: {form_present})")
                        await self.browser.screenshot("booking_page_loaded")
                        return True, "New booking started"

                    logger.warning(f"Click didn't navigate or load form (still on {page.url}), trying next strategy...")
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
        """Select visa application center.

        VFS Angola only has 1 center (Luanda) which is auto-selected.
        The mat-select shows "Portugal Visa Application Center-Luanda"
        and has class ng-valid when pre-selected.
        """
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info(f"Selecting center: {center}")

            # Wait for center dropdown to appear on the form
            try:
                await page.wait_for_selector(Selectors.CENTER_SELECT, timeout=15000)
            except:
                # If center dropdown not found, check if we're on the right page
                body_text = await page.text_content("body") or ""
                if "appointment details" in body_text.lower() or "application centre" in body_text.lower():
                    logger.info("On appointment details page but center dropdown not found via selector")
                    await self.browser.screenshot("center_select_debug")
                    return False, "Center dropdown not found"
                return False, f"Not on booking form page. URL: {page.url}"

            await self.browser.random_delay(500, 1000)

            # Check if already pre-selected (VFS Angola has only Luanda)
            current_value = await page.evaluate("""
                () => {
                    const select = document.querySelector("mat-select[formcontrolname='centerCode']");
                    if (!select) return '';
                    // Check the displayed value text
                    const valueText = select.querySelector('.mat-mdc-select-value-text');
                    if (valueText) return valueText.innerText.trim();
                    // Fallback: check aria-activedescendant
                    return select.getAttribute('aria-activedescendant') || '';
                }
            """)

            if current_value and center.lower() in current_value.lower():
                logger.info(f"Center already pre-selected: {current_value}")
                return True, f"Center already selected: {current_value}"

            # Not pre-selected, click to open dropdown and select
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
        """Select visa category — subcategory auto-populates after selection.

        VFS has 3 appointment categories: Job Seeker, Visto Nacional, Visto Schengen.
        After selecting the category, the system processes and the sub-category
        auto-fills (no manual subcategory selection needed).

        Angular Material mat-select dropdowns render mat-option elements
        in a cdk-overlay-container ONLY when the dropdown is open.
        """
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info(f"Selecting category: {category}")

            # Wait for the form to be fully interactive after page load
            # Angular may still be initializing dropdowns even after they appear in DOM
            await self._wait_for_loading(page)
            await self.browser.random_delay(2000, 3000)

            # Select main category
            await page.wait_for_selector(Selectors.CATEGORY_SELECT, timeout=15000)
            await self.browser.random_delay(500, 1000)

            # Click category dropdown and verify it opened
            success = await self._click_mat_select_and_pick(
                page, Selectors.CATEGORY_SELECT, category, "category"
            )
            if not success:
                return False, f"Failed to select category: {category}"

            # Wait for the system to process — subcategory auto-populates
            logger.info("Waiting for system to process category selection...")
            await self.browser.random_delay(2000, 3000)
            await self._wait_for_loading(page)

            # Check if subcategory was auto-populated
            try:
                sub_select = await page.query_selector(Selectors.SUBCATEGORY_SELECT)
                if sub_select:
                    sub_value = await page.evaluate("""
                        () => {
                            const select = document.querySelector("mat-select[formcontrolname='visaCategoryCode']");
                            if (!select) return '';
                            const valueText = select.querySelector('.mat-mdc-select-value-text');
                            if (valueText) return valueText.innerText.trim();
                            return '';
                        }
                    """)
                    if sub_value:
                        logger.info(f"Subcategory auto-populated: {sub_value}")
                    else:
                        logger.info("Subcategory dropdown present but empty, attempting to select...")
                        # Only manually select if it wasn't auto-populated
                        await self.browser.random_delay(1000, 2000)
                        pick_success = await self._click_mat_select_and_pick(
                            page, Selectors.SUBCATEGORY_SELECT, subcategory, "subcategory"
                        )
                        if not pick_success:
                            logger.warning(f"Could not select subcategory: {subcategory}, continuing anyway...")
                        await self._wait_for_loading(page)
            except Exception as e:
                logger.info(f"Subcategory check: {e}")

            await self.browser.random_delay(1000, 2000)
            await self._wait_for_loading(page)
            await self.browser.screenshot("category_selected")

            logger.info(f"Category selected: {category}")
            return True, f"Category selected: {category}"

        except Exception as e:
            logger.error(f"Failed to select category: {e}")
            await self.browser.screenshot("select_category_error")
            return False, f"Failed to select category: {str(e)}"

    async def _click_mat_select_and_pick(
        self, page: Page, select_selector: str, option_text: str, label: str
    ) -> bool:
        """Click a mat-select dropdown and pick an option by text.

        Angular Material renders mat-option elements inside a
        cdk-overlay-container panel that only exists while the dropdown is open.
        This method:
        1. Clicks the mat-select to open it
        2. Waits for the overlay panel with mat-option elements
        3. Clicks the matching option
        4. Retries with JS fallback if Playwright click doesn't open the dropdown
        """
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"Opening {label} dropdown (attempt {attempt}/{max_attempts})...")

                # Click to open dropdown
                if attempt <= 2:
                    await self.browser.human_click(select_selector)
                else:
                    # JS fallback: dispatch click event directly
                    await page.evaluate(f"""
                        () => {{
                            const el = document.querySelector("{select_selector}");
                            if (el) {{
                                el.click();
                                el.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true}}));
                                el.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true}}));
                            }}
                        }}
                    """)

                await self.browser.random_delay(500, 1000)

                # Wait for overlay panel with options (Angular Material creates this on open)
                overlay_selector = ".cdk-overlay-container mat-option, .mat-mdc-select-panel mat-option"
                try:
                    await page.wait_for_selector(overlay_selector, timeout=5000)
                    logger.info(f"{label} dropdown panel opened")
                except:
                    logger.warning(f"{label} dropdown didn't open on attempt {attempt}")
                    # Close any partial state and retry
                    await page.keyboard.press("Escape")
                    await self.browser.random_delay(1000, 2000)
                    continue

                # Find and click the matching option
                option_selector = f"mat-option:has-text('{option_text}')"
                try:
                    option = await page.wait_for_selector(option_selector, timeout=5000)
                    if option:
                        await option.click()
                        logger.info(f"{label} option selected: {option_text}")
                        return True
                except:
                    pass

                # Fallback: try JS click on the option
                clicked = await page.evaluate(f"""
                    () => {{
                        const options = document.querySelectorAll('mat-option');
                        for (const opt of options) {{
                            const text = opt.textContent?.trim() || '';
                            if (text.includes('{option_text}')) {{
                                opt.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}
                """)
                if clicked:
                    logger.info(f"{label} option selected via JS: {option_text}")
                    return True

                # Log available options for debugging
                available = await page.evaluate("""
                    () => {
                        const options = document.querySelectorAll('mat-option');
                        return Array.from(options).map(o => o.textContent?.trim()).filter(Boolean);
                    }
                """)
                logger.warning(f"Available {label} options: {available}")

                # Close dropdown before retry
                await page.keyboard.press("Escape")
                await self.browser.random_delay(1000, 2000)

            except Exception as e:
                logger.warning(f"{label} dropdown attempt {attempt} error: {e}")
                await self.browser.random_delay(1000, 2000)

        await self.browser.screenshot(f"select_{label}_failed")
        return False

    async def select_payment_mode(self, mode: str = "Multicaixa") -> Tuple[bool, str]:
        """Select payment mode dropdown"""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info(f"Selecting payment mode: {mode}")

            # Wait for payment mode dropdown to appear
            try:
                await page.wait_for_selector(Selectors.PAYMENT_MODE_SELECT, timeout=15000)
            except:
                logger.info("Payment mode dropdown not found, may not be required")
                return True, "Payment mode not required"

            await self.browser.random_delay(1000, 2000)

            success = await self._click_mat_select_and_pick(
                page, Selectors.PAYMENT_MODE_SELECT, mode, "payment_mode"
            )
            if not success:
                return False, f"Failed to select payment mode: {mode}"

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

    async def _fill_input_by_label(self, page, label_text: str, value: str):
        """Fill an input field by finding it relative to its label text.
        VFS uses app-dynamic-control > app-input-control with label divs."""
        # Strategy 1: Find by Playwright label-relative selector
        for sel in [
            f"app-input-control:has(div:text-is('{label_text}')) input[matinput]",
            f"app-input-control:has(:text('{label_text}')) input",
        ]:
            try:
                el = await page.wait_for_selector(sel, timeout=3000)
                if el:
                    await el.click()
                    await el.fill("")
                    await self.browser.human_type(sel, value)
                    logger.info(f"Filled '{label_text}' = {value}")
                    return True
            except Exception:
                continue

        # Strategy 2: Use JS to find input near the label text
        try:
            filled = await page.evaluate("""(args) => {
                const [labelText, value] = args;
                // Find all text nodes that match the label
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    if (walker.currentNode.textContent.trim().startsWith(labelText)) {
                        // Walk up to find the app-dynamic-control container
                        let container = walker.currentNode.parentElement;
                        for (let i = 0; i < 8; i++) {
                            if (!container) break;
                            const input = container.querySelector('input[matinput], input.mat-mdc-input-element');
                            if (input) {
                                input.focus();
                                input.value = '';
                                input.dispatchEvent(new Event('input', {bubbles: true}));
                                // Type each character
                                for (const ch of value) {
                                    input.value += ch;
                                    input.dispatchEvent(new Event('input', {bubbles: true}));
                                }
                                input.dispatchEvent(new Event('change', {bubbles: true}));
                                input.dispatchEvent(new Event('blur', {bubbles: true}));
                                return true;
                            }
                            container = container.parentElement;
                        }
                    }
                }
                return false;
            }""", [label_text, value])
            if filled:
                logger.info(f"Filled '{label_text}' = {value} (via JS)")
                return True
        except Exception as e:
            logger.warning(f"JS fill failed for '{label_text}': {e}")

        logger.warning(f"Could not fill '{label_text}'")
        return False

    async def _fill_ngb_date(self, page, field_id: str, date_value):
        """Fill an ngb-datepicker date field by ID.

        ngb-datepicker has an internal NgbDateStruct model {year, month, day}.
        Just typing text doesn't update the model — must use Angular's API
        or simulate the datepicker calendar selection.
        """
        # Parse date into components
        if isinstance(date_value, date):
            year, month, day = date_value.year, date_value.month, date_value.day
        elif hasattr(date_value, 'year'):
            year, month, day = date_value.year, date_value.month, date_value.day
        else:
            # Parse string like "18/05/1985" or "1985-05-18"
            s = str(date_value)
            if "/" in s:
                parts = s.split("/")
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            elif "-" in s:
                parts = s.split("-")
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            else:
                logger.warning(f"Cannot parse date: {date_value}")
                return False

        date_str = f"{day:02d}/{month:02d}/{year}"

        try:
            # Use Angular's ngbDatepicker API to set the model properly
            success = await page.evaluate("""(args) => {
                const [fieldId, year, month, day, dateStr] = args;
                const input = document.getElementById(fieldId);
                if (!input) return false;

                // Get the Angular component instance via __ngContext__ or ng.getComponent
                // Method 1: Use ng.probe (Angular debug tools)
                try {
                    const ngRef = window.ng;
                    if (ngRef && ngRef.getComponent) {
                        // Walk up from input to find the datepicker component
                        let el = input.closest('app-ngb-datepicker');
                        if (el) {
                            const comp = ngRef.getComponent(el);
                            if (comp && comp.writeValue) {
                                comp.writeValue({year, month, day});
                                if (comp.onChange) comp.onChange({year, month, day});
                                input.value = dateStr;
                                return true;
                            }
                        }
                    }
                } catch(e) {}

                // Method 2: Trigger ngModelChange through input events
                // ngb-datepicker listens for input events and parses via NgbDateParserFormatter
                // The default parser expects yyyy-mm-dd format
                const isoStr = `${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`;

                input.focus();
                // Try setting with ISO format first (default ngb parser)
                input.value = isoStr;
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));

                // Check if Angular picked it up (input might reformat)
                // Small delay then set display format
                setTimeout(() => {
                    // If the value was accepted, ngb may have reformatted it
                    // If not, try the dd/mm/yyyy format
                    if (input.classList.contains('ng-invalid')) {
                        input.value = dateStr;
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    input.dispatchEvent(new Event('blur', {bubbles: true}));
                }, 100);

                return true;
            }""", [field_id, year, month, day, date_str])

            if success:
                logger.info(f"Set date #{field_id} via Angular API: {date_str}")
                await self.browser.random_delay(300, 500)

                # Also try clicking the calendar icon and selecting the date
                # This is the most reliable way to set ngb-datepicker
                try:
                    # Find the calendar toggle button next to this input
                    toggle = await page.query_selector(
                        f"#{field_id} ~ .input-group-addon, "
                        f"app-ngb-datepicker:has(#{field_id}) .input-group-addon"
                    )
                    if toggle:
                        await toggle.click()
                        await self.browser.random_delay(500, 800)

                        # Navigate to the correct month/year and click the day
                        await self._select_datepicker_date(page, year, month, day)
                        logger.info(f"Selected date via calendar: {date_str}")
                except Exception as e:
                    logger.info(f"Calendar selection skipped: {e}")

                return True

        except Exception as e:
            logger.warning(f"Could not fill date #{field_id}: {e}")

        # Last resort: type the date and hope VFS has a custom parser
        try:
            el = await page.wait_for_selector(f"#{field_id}", timeout=3000)
            if el:
                await el.click()
                await el.fill("")
                await page.keyboard.type(date_str, delay=50)
                await page.keyboard.press("Tab")
                await self.browser.random_delay(200, 400)
                logger.info(f"Typed date #{field_id} = {date_str}")
                return True
        except Exception as e:
            logger.warning(f"Typing date failed for #{field_id}: {e}")
            return False

    async def _select_datepicker_date(self, page, year: int, month: int, day: int):
        """Navigate ngb-datepicker calendar to select a specific date."""
        # ngb-datepicker opens a dropdown calendar
        # It has navigation arrows and a month/year selector

        # Click the month/year title to switch to year view for faster navigation
        try:
            # Wait for datepicker dropdown
            await page.wait_for_selector("ngb-datepicker", timeout=3000)

            # Click the navigation label (shows "Month Year") to go to year selection
            nav_label = await page.query_selector(
                "ngb-datepicker-navigation .ngb-dp-navigation-select"
            )
            if nav_label:
                # Use the select dropdowns for month and year
                # Month select
                month_select = await page.query_selector(
                    "ngb-datepicker-navigation select:first-of-type"
                )
                if month_select:
                    await month_select.select_option(str(month))
                    await self.browser.random_delay(200, 400)

                # Year select
                year_select = await page.query_selector(
                    "ngb-datepicker-navigation select:last-of-type"
                )
                if year_select:
                    await year_select.select_option(str(year))
                    await self.browser.random_delay(200, 400)

                # Click the day
                day_btn = await page.query_selector(
                    f"ngb-datepicker-month .ngb-dp-day div:text-is('{day}')"
                )
                if day_btn:
                    await day_btn.click()
                    await self.browser.random_delay(200, 400)
                    return True

        except Exception as e:
            logger.warning(f"Calendar date selection failed: {e}")
            # Close the datepicker
            await page.keyboard.press("Escape")

        return False

    async def fill_applicant_details(self, applicant: Dict) -> Tuple[bool, str]:
        """Fill applicant details form (Your Details page).
        VFS uses app-dynamic-form with app-dynamic-control components.
        Fields have NO formcontrolname — use labels and IDs instead."""
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Filling applicant details...")

            # Wait for the Your Details page to load
            # Check for h1 "Your Details" or the form structure
            form_found = False
            for selector in [
                "h1:text-is('Your Details')",
                "app-applicant-details",
                "input#dateOfBirth",
                "input[placeholder*='first name' i]",
            ]:
                try:
                    await page.wait_for_selector(selector, timeout=15000)
                    logger.info(f"Your Details page detected via: {selector}")
                    form_found = True
                    break
                except Exception:
                    continue

            if not form_found:
                await self.browser.screenshot("form_not_found")
                inputs = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('input, mat-select')).map(el => ({
                        tag: el.tagName, id: el.id,
                        placeholder: el.placeholder || '',
                        class: el.className.substring(0, 60)
                    }))
                }""")
                logger.error(f"Your Details form not found. Elements: {inputs}")
                return False, "Your Details form not found"

            await self.browser.screenshot("form_found")
            logger.info("Form found, filling fields during countdown...")

            # Fill text fields by label
            await self._fill_input_by_label(page, "First Name", applicant.get("first_name", ""))
            await self.browser.random_delay(300, 600)

            await self._fill_input_by_label(page, "Last Name", applicant.get("last_name", ""))
            await self.browser.random_delay(300, 600)

            # Gender dropdown (mat-select — no formcontrolname, find by label)
            if applicant.get("gender"):
                gender_sel = "app-dynamic-control:has(:text('Gender')) mat-select"
                await self._select_dropdown(gender_sel, applicant["gender"])
                await self.browser.random_delay(300, 600)

            # Date of Birth (ngb-datepicker with id="dateOfBirth")
            if applicant.get("date_of_birth"):
                await self._fill_ngb_date(page, "dateOfBirth", applicant["date_of_birth"])
                await self.browser.random_delay(300, 600)

            # Nationality dropdown (mat-select — no formcontrolname, find by label)
            if applicant.get("nationality"):
                nationality_sel = "app-dynamic-control:has(:text('Nationality')) mat-select"
                await self._select_dropdown(nationality_sel, applicant["nationality"])
                await self.browser.random_delay(300, 600)

            # Passport Number
            await self._fill_input_by_label(page, "Passport Number", applicant.get("passport_number", ""))
            await self.browser.random_delay(300, 600)

            # Passport Expiry Date (ngb-datepicker with id="passportExpirtyDate" — VFS typo)
            if applicant.get("passport_expiry"):
                await self._fill_ngb_date(page, "passportExpirtyDate", applicant["passport_expiry"])
                await self.browser.random_delay(300, 600)

            # Contact number: dial code input + phone number input (two separate fields)
            # Dial code field: small input (maxlength=3, placeholder="44") — just the digits
            # Phone field: larger input (maxlength=15, placeholder="012345648382") — just the number
            dial_code = applicant.get("dial_code", "+244").lstrip("+")
            phone = applicant.get("phone", "")

            # Strip dial code prefix from phone number if present
            # e.g., "+244947349423" → "947349423", "244947349423" → "947349423"
            phone_clean = phone.lstrip("+")
            if phone_clean.startswith(dial_code):
                phone_clean = phone_clean[len(dial_code):]
            # Also strip leading zeros that might appear
            phone_clean = phone_clean.lstrip("0") if phone_clean.startswith("0") else phone_clean

            logger.info(f"Contact: dial_code={dial_code}, phone={phone_clean} (raw: {phone})")

            try:
                dial_el = await page.wait_for_selector(
                    "input[placeholder='44'], input[maxlength='3']", timeout=3000
                )
                if dial_el:
                    await dial_el.click()
                    await dial_el.fill("")
                    await page.keyboard.type(dial_code, delay=50)
                    logger.info(f"Filled dial code = {dial_code}")
            except Exception as e:
                logger.warning(f"Could not fill dial code: {e}")
            await self.browser.random_delay(200, 400)

            try:
                phone_el = await page.wait_for_selector(
                    "input[maxlength='15'], input[placeholder*='012345']", timeout=3000
                )
                if phone_el:
                    await phone_el.click()
                    await phone_el.fill("")
                    await page.keyboard.type(phone_clean, delay=30)
                    logger.info(f"Filled phone = {phone_clean}")
            except Exception as e:
                logger.warning(f"Could not fill phone: {e}")
            await self.browser.random_delay(300, 600)

            # Email
            await self._fill_input_by_label(page, "Email", applicant.get("email", ""))
            await self.browser.random_delay(300, 600)

            # Confirm Email (may or may not exist on the form)
            try:
                await self._fill_input_by_label(page, "Confirm Email", applicant.get("email", ""))
            except Exception:
                logger.info("No Confirm Email field found (may not be required)")

            await self.browser.screenshot("details_filled")
            logger.info("All fields filled. Waiting for 28-second countdown to finish...")

            # Wait remaining time for the mandatory countdown before Save is enabled
            await asyncio.sleep(20)

            logger.info("Applicant details filled")
            return True, "Applicant details filled"

        except Exception as e:
            logger.error(f"Failed to fill applicant details: {e}")
            await self.browser.screenshot("fill_details_error")
            return False, f"Failed to fill applicant details: {str(e)}"

    async def save_applicant_details(self) -> Tuple[bool, str]:
        """Click Save → solve Turnstile captcha → click Submit → handle Reminder modal.

        After clicking Save, VFS shows a "Verify Captcha" modal with Cloudflare Turnstile.
        After solving and clicking Submit, a Reminder modal appears ("keep your passport handy").
        """
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Clicking Save button...")

            await page.wait_for_selector(Selectors.SAVE_BUTTON, timeout=10000)
            await self.browser.random_delay(500, 1000)
            await self.browser.human_click(Selectors.SAVE_BUTTON)
            await self.browser.random_delay(2000, 3000)

            await self.browser.screenshot("save_clicked")

            # Step 1: Handle Turnstile captcha modal ("Verify Captcha")
            logger.info("Checking for Verify Captcha modal...")
            try:
                captcha_modal = await page.wait_for_selector(
                    "h4:text-is('Verify Captcha'), "
                    "mat-dialog-container:has-text('Verify Captcha'), "
                    ".mat-mdc-dialog-title:has-text('Verify Captcha')",
                    timeout=10000,
                )
                if captcha_modal:
                    logger.info("Verify Captcha modal detected - solving Turnstile...")
                    await self.browser.screenshot("captcha_modal_found")

                    # Solve the Turnstile within the modal
                    from .turnstile import TurnstileSolver
                    solver = TurnstileSolver()
                    solved = await solver.solve(page)

                    if solved:
                        logger.info("Turnstile solved, clicking Submit...")
                    else:
                        logger.warning("Turnstile may not be solved, trying Submit anyway...")

                    await self.browser.random_delay(1000, 2000)

                    # Click Submit button in the captcha modal
                    submit_selectors = [
                        "mat-dialog-container button:has-text('Submit')",
                        ".cdk-overlay-container button:has-text('Submit')",
                        "app-captcha-modal button:has-text('Submit')",
                        "button:has-text('Submit')",
                    ]
                    submitted = False
                    for sel in submit_selectors:
                        try:
                            btn = await page.query_selector(sel)
                            if btn and await btn.is_visible():
                                await btn.click()
                                logger.info("Submit button clicked")
                                submitted = True
                                break
                        except Exception:
                            continue

                    if not submitted:
                        logger.warning("Could not click Submit button")
                        await self.browser.screenshot("submit_not_found")

                    await self.browser.random_delay(2000, 3000)
                    await self._wait_for_loading(page)
            except Exception as e:
                logger.info(f"No Verify Captcha modal: {e}")

            await self.browser.screenshot("after_captcha_submit")

            # Step 2: Handle Reminder modal ("Please keep your passport handy")
            logger.info("Checking for Reminder modal...")
            try:
                # Wait for the Reminder modal (app-same-passport-modal)
                reminder = await page.wait_for_selector(
                    "app-same-passport-modal, "
                    "mat-dialog-container:has-text('Reminder'), "
                    "mat-dialog-container:has-text('passport handy')",
                    timeout=10000,
                )
                if reminder:
                    logger.info("Reminder modal detected")
                    await self.browser.screenshot("reminder_modal")

                    # Click Continue button in the modal
                    continue_selectors = [
                        "app-same-passport-modal button:has-text('Continue')",
                        "mat-dialog-container button:has-text('Continue')",
                        ".cdk-overlay-container button:has-text('Continue')",
                    ]
                    for sel in continue_selectors:
                        try:
                            btn = await page.query_selector(sel)
                            if btn and await btn.is_visible():
                                await btn.click()
                                logger.info("Reminder Continue clicked")
                                break
                        except Exception:
                            continue

                    await self.browser.random_delay(2000, 3000)
                    await self._wait_for_loading(page)
            except Exception as e:
                logger.info(f"No Reminder modal: {e}")

            # Step 3: Wait for page transition after save
            # After Reminder modal Continue, the page should redirect to
            # idnvui.vfsglobal.com (identity verification) or stay on VFS
            logger.info("Waiting for page transition after save...")
            await self.browser.random_delay(2000, 3000)

            # Check current page state
            current_url = page.url
            logger.info(f"Current URL after save: {current_url}")
            await self.browser.screenshot("details_saved")
            logger.info("Applicant details saved successfully")
            return True, "Details saved"

        except Exception as e:
            logger.error(f"Failed to save details: {e}")
            await self.browser.screenshot("save_error")
            return False, f"Failed to save details: {str(e)}"

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

    async def _inject_fake_camera(self, page, file_path: str) -> bool:
        """Override getUserMedia to serve a video or image as fake camera feed.

        For VIDEO files (.mp4, .webm): creates a hidden <video> element, plays it
        in a loop, and draws frames to canvas → captureStream.

        For IMAGE files (.jpg, .png): draws the static image to canvas → captureStream.

        The canvas stream replaces the real camera via navigator.mediaDevices.getUserMedia override.
        """
        import base64

        fpath = Path(file_path)
        if not fpath.exists():
            logger.warning(f"File not found for fake camera: {file_path}")
            return False

        with open(fpath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        ext = fpath.suffix.lower()
        is_video = ext in (".mp4", ".webm", ".ogg", ".mkv")

        if is_video:
            mime = {".mp4": "video/mp4", ".webm": "video/webm", ".ogg": "video/ogg"}.get(ext, "video/mp4")
            js = f"""
            (function() {{
                // Create hidden video element
                const video = document.createElement('video');
                video.src = 'data:{mime};base64,{b64}';
                video.loop = true;
                video.muted = true;
                video.playsInline = true;
                video.style.display = 'none';
                document.body.appendChild(video);

                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');

                video.addEventListener('loadedmetadata', function() {{
                    canvas.width = video.videoWidth || 640;
                    canvas.height = video.videoHeight || 480;
                    video.play();
                }});

                video.addEventListener('play', function() {{
                    const stream = canvas.captureStream(30);

                    // Add audio track if present
                    const videoStream = video.captureStream ? video.captureStream() : null;
                    if (videoStream) {{
                        const audioTracks = videoStream.getAudioTracks();
                        audioTracks.forEach(t => stream.addTrack(t));
                    }}

                    // Override getUserMedia
                    const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                    navigator.mediaDevices.getUserMedia = function(constraints) {{
                        if (constraints && constraints.video) {{
                            return Promise.resolve(stream);
                        }}
                        return origGetUserMedia(constraints);
                    }};

                    // Keep drawing video frames to canvas
                    function drawFrame() {{
                        if (!video.paused && !video.ended) {{
                            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                        }}
                        requestAnimationFrame(drawFrame);
                    }}
                    drawFrame();
                }});

                video.play().catch(e => console.warn('Video autoplay failed:', e));
                window.__fakeCameraActive = true;
                window.__fakeCameraVideo = video;
            }})();
            """
        else:
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            js = f"""
            (function() {{
                const dataUrl = 'data:{mime};base64,{b64}';
                const img = new Image();
                img.src = dataUrl;
                img.onload = function() {{
                    const canvas = document.createElement('canvas');
                    canvas.width = img.width;
                    canvas.height = img.height;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);

                    const stream = canvas.captureStream(30);
                    const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);

                    navigator.mediaDevices.getUserMedia = function(constraints) {{
                        if (constraints && constraints.video) {{
                            return Promise.resolve(stream);
                        }}
                        return origGetUserMedia(constraints);
                    }};

                    // Keep redrawing to keep stream alive
                    setInterval(() => {{ ctx.drawImage(img, 0, 0); }}, 100);
                }};
                window.__fakeCameraActive = true;
            }})();
            """
        try:
            await page.evaluate(js)
            media_type = "video" if is_video else "image"
            logger.info(f"Injected fake camera with {media_type}: {fpath.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to inject fake camera: {e}")
            return False

    async def _click_mui_continue(self, page, context: str = "") -> bool:
        """Click a MUI Continue button on idnvui.vfsglobal.com pages."""
        selectors = [
            "button.MuiButton-containedPrimary:has-text('Continue')",
            "button.MuiButton-root:has-text('Continue')",
            "button:has-text('CONTINUE')",
            "button:has-text('Continue')",
        ]
        for sel in selectors:
            try:
                btn = await page.wait_for_selector(sel, timeout=5000)
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info(f"Clicked Continue{' - ' + context if context else ''}")
                    return True
            except Exception:
                continue
        logger.warning(f"Could not find Continue button{' - ' + context if context else ''}")
        return False

    async def handle_identity_verification(self, applicant: Optional[Dict] = None) -> Tuple[bool, str]:
        """Handle identity verification on idnvui.vfsglobal.com.

        Full flow:
        1. "Start Identity Verification" page → click CONTINUE
        2. Camera permission → face liveness check ("Move face in front of camera")
        3. "Start Passport Verification" page → click CONTINUE
        4. Passport front capture → click CAPTURE (10s countdown)
        5. Passport photo page capture → click CAPTURE (10s countdown)
        6. "Security check completed" → redirects back to visa.vfsglobal.com
        """
        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Checking for identity verification redirect...")

            # Wait up to 15 seconds for potential redirect to idnvui.vfsglobal.com
            # The redirect happens after Reminder modal Continue click
            for i in range(15):
                await asyncio.sleep(1)
                if "idnvui.vfsglobal.com" in page.url:
                    break
                # Also check if the page content changed to show verification
                if i == 5:
                    logger.info(f"Still on: {page.url} (waiting for redirect...)")

            if "idnvui.vfsglobal.com" not in page.url:
                logger.info(f"No identity verification redirect detected (URL: {page.url})")
                return True, "No identity verification needed"

            logger.info(f"Identity verification page detected: {page.url}")
            await self.browser.screenshot("idv_start")

            # Check if applicant has uploaded files
            face_videos = applicant.get("face_videos", []) if applicant else []
            # Fallback: single face_photo_path (legacy)
            if not face_videos:
                face_photo = applicant.get("face_photo_path") if applicant else None
                if face_photo:
                    face_videos = [face_photo]
            passport_front = applicant.get("passport_front_path") if applicant else None
            passport_page = applicant.get("passport_page_path") if applicant else None
            has_photos = len(face_videos) > 0 or passport_front or passport_page

            if has_photos:
                logger.info(f"Applicant has {len(face_videos)} face video(s), "
                            f"passport_front={'yes' if passport_front else 'no'}, "
                            f"passport_page={'yes' if passport_page else 'no'}")
            else:
                logger.info("No uploaded photos/videos - waiting for manual verification")

            # ============================================================
            # Step 1+2: Face liveness check — cycle through videos on failure
            # ============================================================
            face_liveness_passed = False
            for video_idx, face_video in enumerate(face_videos or [None]):
                video_num = video_idx + 1
                total_videos = len(face_videos)

                if face_video:
                    logger.info(f"Face liveness attempt {video_num}/{total_videos}: {Path(face_video).name}")
                else:
                    logger.info("No face video — waiting for manual liveness check")

                # Wait for "Start Identity Verification" page
                try:
                    await page.wait_for_selector(
                        "h1:has-text('Start Identity Verification'), "
                        "h1:has-text('Identity Verification')",
                        timeout=15000,
                    )
                    logger.info("'Start Identity Verification' page loaded")
                    await self.browser.screenshot(f"idv_face_attempt_{video_num}")
                    await self.browser.random_delay(1000, 2000)

                    # Inject fake camera BEFORE clicking Continue
                    if face_video:
                        await self._inject_fake_camera(page, face_video)
                        logger.info(f"Injected face video #{video_num} as camera")

                    await self._click_mui_continue(page, "Start Identity Verification")
                    await self.browser.random_delay(2000, 3000)
                except Exception as e:
                    logger.warning(f"Start Identity Verification not found: {e}")

                # Face liveness check — camera active
                logger.info("Face liveness check - camera should be active...")
                await self.browser.screenshot("idv_step2_face_camera")

                # Re-inject in case page reset the override
                if face_video:
                    await asyncio.sleep(1)
                    await self._inject_fake_camera(page, face_video)

                # Wait for result (up to 90 seconds per attempt)
                logger.info(f"Waiting for face liveness result (attempt {video_num})...")
                try:
                    result = await page.wait_for_function(
                        """() => {
                            const text = document.body?.innerText?.toLowerCase() || '';
                            if (text.includes('passport verification') ||
                                text.includes('start passport') ||
                                text.includes('security check completed') ||
                                text.includes('verification successful')) return 'passed';
                            if (text.includes('verification failed') ||
                                text.includes('liveness failed') ||
                                text.includes('try again')) return 'failed';
                            if (window.location.href.includes('visa.vfsglobal.com')) return 'redirect';
                            return null;
                        }""",
                        timeout=90000,
                    )
                    result_val = await result.json_value() if result else None
                except Exception:
                    result_val = None
                    logger.warning("Face liveness timeout (90s)")

                await self.browser.screenshot(f"idv_face_result_{video_num}")

                if result_val == "passed" or result_val == "redirect":
                    logger.info(f"Face liveness PASSED with video #{video_num}")
                    face_liveness_passed = True
                    break
                elif result_val == "failed" and video_idx < total_videos - 1:
                    logger.warning(f"Face liveness FAILED with video #{video_num} — trying next video")
                    # Look for a retry/try again button
                    for retry_sel in [
                        "button:has-text('Try Again')", "button:has-text('RETRY')",
                        "button:has-text('Retry')", "button:has-text('Start Over')",
                    ]:
                        try:
                            btn = await page.query_selector(retry_sel)
                            if btn and await btn.is_visible():
                                await btn.click()
                                logger.info("Clicked retry for next video attempt")
                                await self.browser.random_delay(2000, 3000)
                                break
                        except Exception:
                            continue
                    continue  # Try next video
                else:
                    logger.warning(f"Face liveness result: {result_val} (video #{video_num})")
                    break

            if not face_liveness_passed and not face_videos:
                # No videos — wait for manual completion (up to 3 min)
                logger.info("Waiting for manual face liveness (up to 3 min)...")
                try:
                    await page.wait_for_function(
                        """() => {
                            const text = document.body?.innerText?.toLowerCase() || '';
                            return text.includes('passport verification') ||
                                   text.includes('security check completed') ||
                                   window.location.href.includes('visa.vfsglobal.com');
                        }""",
                        timeout=180000,
                    )
                    face_liveness_passed = True
                except Exception:
                    logger.warning("Manual face liveness timeout")
                    await self.browser.screenshot("idv_face_manual_timeout")

            await self.browser.screenshot("idv_step2_face_done")

            # Check if already redirected back (no passport step needed)
            if "visa.vfsglobal.com" in page.url:
                logger.info("Redirected back to VFS after face check")
                return True, "Identity verification completed"

            # ============================================================
            # Step 3: "Start Passport Verification" page → click CONTINUE
            # ============================================================
            body_text = (await page.text_content("body") or "").lower()
            if "passport verification" in body_text or "start passport" in body_text:
                logger.info("'Start Passport Verification' page detected")
                await self.browser.screenshot("idv_step3_passport_start")
                await self.browser.random_delay(1000, 2000)

                # Inject passport front photo BEFORE clicking Continue
                # so the camera feed shows the passport when the camera activates
                if passport_front:
                    await self._inject_fake_camera(page, passport_front)
                    logger.info("Passport front photo injected as camera feed (before Continue)")

                await self._click_mui_continue(page, "Start Passport Verification")
                await self.browser.random_delay(2000, 3000)

                # ============================================================
                # Step 4: Passport front — AUTO-DETECTION (no Capture button)
                # Camera activates, system auto-scans for passport front.
                # If it sees a face → "Verification terminated" → RETRY
                # If it detects passport → moves to Step 2 automatically
                # ============================================================
                logger.info("Step 1: Display front of passport — waiting for auto-detection...")
                await self.browser.screenshot("idv_step4_passport_front_camera")

                # Re-inject passport front after camera activates (camera stream may reset)
                if passport_front:
                    await asyncio.sleep(2)
                    await self._inject_fake_camera(page, passport_front)
                    logger.info("Re-injected passport front photo after camera activation")

                # Wait for either: "Passport Detected!" / Step 2 / "Verification terminated"
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        result = await page.wait_for_function(
                            """() => {
                                const text = document.body?.innerText?.toLowerCase() || '';
                                if (text.includes('passport detected')) return 'detected';
                                if (text.includes('step 2')) return 'step2';
                                if (text.includes('open passport')) return 'step2';
                                if (text.includes('verification terminated')) return 'terminated';
                                if (text.includes('security check')) return 'done';
                                if (window.location.href.includes('visa.vfsglobal.com')) return 'done';
                                return false;
                            }""",
                            timeout=60000,
                        )
                        status = await result.json_value() if result else "timeout"
                    except Exception:
                        status = "timeout"

                    logger.info(f"Passport front detection result: {status} (attempt {attempt + 1})")
                    await self.browser.screenshot(f"idv_step4_result_{status}_{attempt}")

                    if status == "terminated":
                        # "Verification terminated" — click RETRY
                        logger.warning("Verification terminated — clicking RETRY")
                        try:
                            retry_btn = await page.wait_for_selector(
                                "button:has-text('RETRY'), button:has-text('Retry')",
                                timeout=5000,
                            )
                            if retry_btn and await retry_btn.is_visible():
                                # Re-inject passport photo before retrying
                                if passport_front:
                                    await self._inject_fake_camera(page, passport_front)
                                await retry_btn.click()
                                logger.info("Clicked RETRY")
                                await self.browser.random_delay(2000, 3000)
                                # Re-inject again after retry resets camera
                                if passport_front:
                                    await asyncio.sleep(2)
                                    await self._inject_fake_camera(page, passport_front)
                                continue  # Try again
                        except Exception:
                            logger.warning("Could not click RETRY button")
                            break
                    else:
                        # detected / step2 / done — proceed
                        break

                await self.browser.screenshot("idv_step4_front_done")

                # ============================================================
                # Step 5: Passport photo page — click CAPTURE (10s countdown)
                # After Step 1 succeeds, shows "Passport Detected!" then
                # "Step 2: Open passport to display the photo page"
                # User must click CAPTURE to start 10-second countdown
                # ============================================================
                if "visa.vfsglobal.com" not in page.url:
                    body_text = (await page.text_content("body") or "").lower()
                    if ("step 2" in body_text or "photo page" in body_text or
                            "open passport" in body_text or "passport detected" in body_text):
                        logger.info("Step 2: Passport photo page — looking for CAPTURE button...")
                        await self.browser.screenshot("idv_step5_photo_page")

                        # Inject passport photo page as camera feed
                        if passport_page:
                            await asyncio.sleep(1)
                            await self._inject_fake_camera(page, passport_page)
                            logger.info("Passport photo page injected as camera feed")

                        # Click CAPTURE button to start 10-second countdown
                        try:
                            capture_btn = await page.wait_for_selector(
                                "button:has-text('CAPTURE'), button:has-text('Capture')",
                                timeout=15000,
                            )
                            if capture_btn and await capture_btn.is_visible():
                                # Re-inject right before capture to ensure fresh feed
                                if passport_page:
                                    await self._inject_fake_camera(page, passport_page)
                                await capture_btn.click()
                                logger.info("Clicked CAPTURE — 10 second countdown started")
                                # Wait for 10-second countdown to complete
                                await asyncio.sleep(12)
                        except Exception as e:
                            logger.warning(f"CAPTURE button not found: {e}")

                        await self.browser.screenshot("idv_step5_after_capture")

                        # Wait for photo page capture to complete
                        try:
                            await page.wait_for_function(
                                """() => {
                                    const text = document.body?.innerText?.toLowerCase() || '';
                                    return text.includes('security check') ||
                                           text.includes('verification successful') ||
                                           text.includes('verified') ||
                                           text.includes('processing') ||
                                           window.location.href.includes('visa.vfsglobal.com');
                                }""",
                                timeout=60000,
                            )
                        except Exception:
                            logger.warning("Photo page capture completion timeout")

                        await self.browser.screenshot("idv_step5_done")

            # ============================================================
            # Step 6: Wait for verification to complete (up to 5 minutes)
            # ============================================================
            if "idnvui.vfsglobal.com" in page.url:
                logger.info("Waiting for identity verification to complete (up to 5 minutes)...")
                try:
                    await page.wait_for_function(
                        """() => {
                            const text = document.body?.innerText?.toLowerCase() || '';
                            return text.includes('security check completed') ||
                                   text.includes('verification successful') ||
                                   text.includes('verified successfully') ||
                                   window.location.href.includes('visa.vfsglobal.com');
                        }""",
                        timeout=300000,  # 5 minutes
                    )
                    logger.info("Verification completed!")
                except Exception:
                    logger.warning("Identity verification timeout (5 min)")
                    await self.browser.screenshot("idv_timeout")
                    return False, "Identity verification timeout"

                # Click Continue to redirect back to VFS
                if "idnvui.vfsglobal.com" in page.url:
                    await self.browser.random_delay(1000, 2000)
                    await self._click_mui_continue(page, "verification complete → back to VFS")
                    await self.browser.random_delay(3000, 5000)

            await self.browser.screenshot("idv_complete")
            logger.info("Identity verification completed")
            return True, "Identity verification completed"

        except Exception as e:
            logger.error(f"Identity verification error: {e}")
            await self.browser.screenshot("idv_error")
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

        # Phase 7: Identity Verification (pass applicant for uploaded photos)
        success, message = await self.handle_identity_verification(applicant)
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
        """Select value from Angular Material dropdown (mat-select).

        VFS uses app-dropdown > mat-select. Clicking mat-select opens a
        cdk-overlay panel with mat-option elements.
        """
        page = self.browser.page
        if not page:
            return

        try:
            # Try to find and click the mat-select
            el = await page.wait_for_selector(selector, timeout=5000)
            if not el:
                logger.warning(f"Dropdown not found: {selector}")
                return

            # Click to open the dropdown panel
            await el.click()
            await self.browser.random_delay(500, 1000)

            # Wait for the overlay panel with options
            option_selector = f"mat-option:has-text('{value}')"
            try:
                option = await page.wait_for_selector(option_selector, timeout=5000)
                if option:
                    await option.click()
                    logger.info(f"Selected '{value}' from dropdown")
                    await self.browser.random_delay(500, 1000)
                    return
            except Exception:
                pass

            # Fallback: try clicking the mat-select trigger area directly
            logger.info(f"Retrying dropdown click for '{value}'...")
            # Close any open overlay first
            await page.keyboard.press("Escape")
            await self.browser.random_delay(300, 500)

            # Try clicking via the .mat-mdc-select-trigger inside the mat-select
            trigger_sel = f"{selector} .mat-mdc-select-trigger"
            try:
                trigger = await page.query_selector(trigger_sel)
                if trigger:
                    await trigger.click()
                else:
                    await el.click()
            except Exception:
                await el.click()

            await self.browser.random_delay(500, 1000)

            # Try finding option in the overlay container
            try:
                option = await page.wait_for_selector(
                    f".cdk-overlay-container {option_selector}", timeout=5000
                )
                if option:
                    await option.click()
                    logger.info(f"Selected '{value}' from dropdown (via overlay)")
                    await self.browser.random_delay(500, 1000)
                    return
            except Exception:
                pass

            # Last resort: use JS to select the value via Angular
            logger.info(f"Trying JS selection for '{value}'...")
            await page.evaluate(f"""(value) => {{
                // Find all mat-options in the overlay
                const options = document.querySelectorAll('mat-option');
                for (const opt of options) {{
                    if (opt.textContent.trim().includes(value)) {{
                        opt.click();
                        return true;
                    }}
                }}
                return false;
            }}""", value)
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
