"""
Login Automation - Handles VFS Global login process including OTP

Flow (SPA-based to avoid Cloudflare 403201 on /login):
1. Navigate to /book-an-appointment (Cloudflare allows this)
2. Handle cookie consent ("Accept All Cookies")
3. Navigate to /login WITHIN the SPA (same tab, no new HTTP request)
   - Strategy 1: Angular router via pushState + popstate (best - zero HTTP)
   - Strategy 2: Remove target="_blank" from link, click in same tab
   - Strategy 3: Direct page.goto as last resort
4. Wait for Angular to render login form
5. Enter email + password
6. Turnstile appears AFTER entering credentials → solve it
7. Click "Sign In"
8. OTP page appears → read OTP from email via IMAP
9. Enter OTP, Turnstile appears again → solve it
10. Click "Sign In" → navigates to /dashboard
"""
import asyncio
import re
from typing import Optional, Tuple, Callable
from playwright.async_api import Page
from loguru import logger

from ..app.config import settings, VFSUrls, Selectors
from .browser import BrowserManager
from .turnstile import TurnstileSolver


class LoginAutomation:
    """Handles VFS Global login automation with OTP support"""

    def __init__(self, browser: BrowserManager):
        self.browser = browser
        self.turnstile = TurnstileSolver()
        self._otp_callback: Optional[Callable] = None

    def set_otp_callback(self, callback: Callable):
        """Set callback for OTP retrieval (e.g., from email or Telegram)"""
        self._otp_callback = callback

    async def login(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Perform login to VFS Global (handles email/password + OTP)

        Returns:
            Tuple of (success: bool, message: str)
        """
        email = email or settings.vfs_email
        password = password or settings.vfs_password

        if not email or not password:
            return False, "Email and password are required"

        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Starting login process...")

            # ============================================================
            # Step 1: Navigate to /book-an-appointment (with retry)
            # ============================================================
            max_retries = 3
            page_loaded = False

            for attempt in range(1, max_retries + 1):
                logger.info(f"Navigating to {VFSUrls.BOOK_APPOINTMENT} (attempt {attempt}/{max_retries})")
                await page.goto(VFSUrls.BOOK_APPOINTMENT, wait_until="domcontentloaded", timeout=30000)
                await self.browser.random_delay(5000, 8000)

                if await self._is_blocked_page(page):
                    logger.warning(f"Cloudflare 403 detected (attempt {attempt}/{max_retries})")
                    if attempt < max_retries:
                        wait_time = 10 * attempt
                        logger.info(f"Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        logger.error("All attempts blocked by Cloudflare")
                        await self.browser.screenshot("cloudflare_blocked")
                        return False, "Blocked by Cloudflare protection. Try again later."

                if await self._is_session_expired_page(page):
                    logger.info("Session expired page detected, clearing storage...")
                    await self._clear_all_storage(page)
                    await page.goto(VFSUrls.BOOK_APPOINTMENT, wait_until="domcontentloaded", timeout=30000)
                    await self.browser.random_delay(5000, 8000)

                page_loaded = True
                break

            if not page_loaded:
                return False, "Failed to load VFS page after retries"

            # ============================================================
            # Step 2: Handle cookie consent
            # ============================================================
            await self._handle_cookie_consent(page)

            # ============================================================
            # Step 3: Navigate to /login in SAME TAB (avoid new tab → 403)
            # The "Book now" link has target="_blank" which opens a NEW TAB.
            # New tabs send Sec-Fetch-Site:none + no Referer → Cloudflare 403.
            # Same-tab navigation sends Sec-Fetch-Site:same-origin + Referer
            # plus the existing cf_clearance cookie → passes Cloudflare.
            # ============================================================
            logger.info("Navigating to /login in same tab...")

            book_now_selectors = [
                "a.lets-get-started",
                "a:has-text('Book now')",
                "a:has-text('Book Now')",
                "a:has-text('Reservar agora')",
            ]

            book_now_btn = None
            for selector in book_now_selectors:
                try:
                    book_now_btn = await page.wait_for_selector(selector, timeout=10000)
                    if book_now_btn:
                        logger.info(f"Book now button found with: {selector}")
                        break
                except:
                    continue

            if not book_now_btn:
                await self.browser.screenshot("book_now_not_found")
                return False, "Book now button not found"

            # Remove cookie overlays that might block the click
            await self._remove_cookie_overlays(page)
            await self.browser.random_delay(500, 1000)

            href = await book_now_btn.get_attribute("href")
            target = await book_now_btn.get_attribute("target")
            logger.info(f"Book now link - href: {href}, target: {target}")

            # --- Strategy 1: Remove target="_blank" + override window.open, click same tab ---
            # Clicking in same tab sends proper HTTP headers that pass Cloudflare:
            #   Sec-Fetch-Site: same-origin (not "none" like new tab/address bar)
            #   Referer: .../book-an-appointment
            #   Cookie: cf_clearance=... (from initial page load)
            # Also override window.open in case Angular's JS handler opens new tab
            login_reached = False
            try:
                logger.info("Strategy 1: Same-tab click (remove target + override window.open)...")
                await page.evaluate("""
                    () => {
                        // Remove target="_blank" from login links
                        const links = document.querySelectorAll('a[target="_blank"]');
                        for (const link of links) {
                            if (link.href && (link.href.includes('/login') ||
                                link.classList.contains('lets-get-started'))) {
                                link.removeAttribute('target');
                            }
                        }
                        // Override window.open to prevent new tab if JS handler opens one
                        window.open = function(url) {
                            window.location.href = url;
                            return window;
                        };
                    }
                """)
                await self.browser.random_delay(300, 600)

                # Click the modified link - navigates in same tab
                await book_now_btn.click(timeout=10000)

                # Wait for navigation (full HTTP page load)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                except:
                    pass
                await self.browser.random_delay(3000, 5000)

                if "/login" in page.url and not await self._is_blocked_page(page):
                    login_reached = True
                    logger.info(f"Strategy 1 succeeded. URL: {page.url}")
                elif await self._is_blocked_page(page):
                    logger.warning("Strategy 1: Blocked by Cloudflare")
                    await self.browser.screenshot("strategy1_blocked")
                else:
                    logger.warning(f"Strategy 1: Unexpected URL: {page.url}")
            except Exception as e:
                logger.warning(f"Strategy 1 failed: {e}")

            # --- Strategy 2: location.href from page context (same-origin headers) ---
            # page.evaluate('location.href=...') sends Sec-Fetch-Site:same-origin
            # unlike page.goto() which sends Sec-Fetch-Site:none (like address bar)
            if not login_reached:
                try:
                    logger.info("Strategy 2: location.href navigation (same-origin)...")
                    # Return to appointment page for clean state/Referer if needed
                    if "/book-an-appointment" not in page.url:
                        await page.goto(VFSUrls.BOOK_APPOINTMENT, wait_until="domcontentloaded", timeout=30000)
                        await self.browser.random_delay(3000, 5000)
                    # Navigate via JS - browser sends same-origin headers
                    login_url = VFSUrls.LOGIN
                    await page.evaluate(f"() => {{ window.location.href = '{login_url}'; }}")
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=30000)
                    except:
                        pass
                    await self.browser.random_delay(3000, 5000)

                    if "/login" in page.url and not await self._is_blocked_page(page):
                        login_reached = True
                        logger.info(f"Strategy 2 succeeded. URL: {page.url}")
                    elif await self._is_blocked_page(page):
                        logger.warning("Strategy 2: Blocked by Cloudflare")
                        await self.browser.screenshot("strategy2_blocked")
                except Exception as e:
                    logger.warning(f"Strategy 2 failed: {e}")

            # --- Strategy 3: Direct page.goto (last resort) ---
            # Sends Sec-Fetch-Site:none - most likely to be blocked but worth trying
            if not login_reached:
                logger.warning("Strategy 3: Direct page.goto (last resort)...")
                await page.goto(VFSUrls.LOGIN, wait_until="domcontentloaded", timeout=30000)
                await self.browser.random_delay(3000, 5000)

                if await self._is_blocked_page(page):
                    await self.browser.screenshot("login_blocked")
                    return False, "Login page blocked by Cloudflare (all strategies failed)"

            # ============================================================
            # Step 4: Wait for Angular to render the login form
            # After SPA navigation, Angular needs time to render the component
            # (loading spinner → template placeholders → full form)
            # ============================================================
            logger.info("Waiting for login form to render...")
            await self.browser.random_delay(3000, 5000)

            # Check if page got blocked
            if await self._is_blocked_page(page):
                await self.browser.screenshot("login_page_blocked")
                return False, "Login page blocked by Cloudflare (403201)"

            await self.browser.screenshot("login_page_loaded")
            logger.info(f"Current URL: {page.url}")

            # Check if already logged in
            if await self._is_logged_in(page):
                logger.info("Already logged in")
                return True, "Already logged in"

            # Wait for login form with multiple possible selectors
            login_selectors = [
                "input[placeholder*='jane.doe']",  # Seen in screenshots
                "input[placeholder*='mail']",
                Selectors.EMAIL_INPUT,
                "input[type='email']",
                "input[formcontrolname='username']",
                "input[formcontrolname='email']",
            ]

            email_input = None
            for selector in login_selectors:
                try:
                    email_input = await page.wait_for_selector(selector, timeout=10000)
                    if email_input:
                        logger.info(f"Login form found with selector: {selector}")
                        break
                except:
                    continue

            if not email_input:
                await self.browser.screenshot("login_form_not_found")
                title = await page.title()
                logger.error(f"Login form not found. URL: {page.url}, Title: {title}")
                return False, f"Login form not found. URL: {page.url}"

            # ============================================================
            # Step 5: Enter email + password
            # ============================================================
            logger.info("Entering email...")
            await email_input.click()
            await self.browser.random_delay(300, 600)
            await page.keyboard.type(email, delay=50)
            await self.browser.random_delay(500, 1000)

            # Find and fill password
            logger.info("Entering password...")
            password_input = await page.query_selector(
                f"{Selectors.PASSWORD_INPUT}, input[type='password']"
            )
            if password_input:
                await password_input.click()
                await self.browser.random_delay(300, 600)
                await page.keyboard.type(password, delay=50)
                await self.browser.random_delay(500, 1000)
            else:
                return False, "Password field not found"

            await self.browser.screenshot("credentials_entered")

            # ============================================================
            # Step 6: Turnstile appears AFTER entering credentials
            # Wait for it to appear and solve it
            # ============================================================
            logger.info("Waiting for Turnstile to appear...")
            await self.browser.random_delay(2000, 3000)

            # Wait for Turnstile widget to appear (it shows after filling credentials)
            turnstile_solved = await self._wait_and_solve_turnstile(page)
            if turnstile_solved:
                logger.info("Turnstile solved on login page")
            else:
                logger.info("No Turnstile detected or already passed")

            await self.browser.screenshot("before_sign_in")

            # ============================================================
            # Step 7: Click "Sign In" button
            # ============================================================
            logger.info("Clicking Sign In button...")
            sign_in_selectors = [
                Selectors.SIGN_IN_BUTTON,
                "button:has-text('Sign In')",
                "button:has-text('Sign in')",
                "button[type='submit']",
            ]

            sign_in_clicked = False
            for selector in sign_in_selectors:
                try:
                    btn = await page.wait_for_selector(selector, timeout=5000)
                    if btn:
                        # Wait for button to be enabled (enabled after Turnstile passes)
                        await self.browser.random_delay(1000, 2000)
                        await btn.click()
                        logger.info(f"Sign In clicked with: {selector}")
                        sign_in_clicked = True
                        break
                except:
                    continue

            if not sign_in_clicked:
                await self.browser.screenshot("sign_in_button_not_found")
                return False, "Sign In button not found or not clickable"

            await self.browser.random_delay(3000, 5000)
            await self.browser.screenshot("after_sign_in")

            # ============================================================
            # Step 8: Check for OTP page
            # ============================================================
            if await self._is_otp_page(page):
                logger.info("OTP page detected - 2FA required")
                success, message = await self._handle_otp(page)
                if not success:
                    return False, message

            # ============================================================
            # Step 9: Wait for dashboard
            # ============================================================
            await self._wait_for_login_result(page)

            if await self._is_logged_in(page):
                logger.info("Login successful!")
                await self.browser.screenshot("login_success")
                return True, "Login successful"
            else:
                error = await self._get_error_message(page)
                logger.error(f"Login failed: {error}")
                await self.browser.screenshot("login_failed")
                return False, f"Login failed: {error}"

        except Exception as e:
            logger.error(f"Login error: {e}")
            await self.browser.screenshot("login_error")
            return False, f"Login error: {str(e)}"

    async def _wait_and_solve_turnstile(self, page: Page, timeout: int = 30) -> bool:
        """
        Wait for Turnstile to appear and solve it.
        Turnstile appears AFTER entering credentials on VFS login page.
        Returns True if Turnstile was found and solved.
        """
        try:
            # Wait for Turnstile widget to appear
            turnstile_selectors = [
                "iframe[src*='challenges.cloudflare.com']",
                ".cf-turnstile",
                "[data-sitekey]",
            ]

            turnstile_found = False
            for selector in turnstile_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=timeout * 1000)
                    turnstile_found = True
                    logger.info(f"Turnstile widget found: {selector}")
                    break
                except:
                    continue

            if not turnstile_found:
                return False

            # Check if Turnstile auto-solved (shows "Success!" for real users)
            await self.browser.random_delay(3000, 5000)

            # Check for success state
            success = await page.evaluate("""
                () => {
                    const body = document.body.innerText;
                    return body.includes('Success') || body.includes('success');
                }
            """)

            if success:
                logger.info("Turnstile auto-solved (Success!)")
                return True

            # If not auto-solved, use 2Captcha API
            logger.info("Turnstile not auto-solved, using 2Captcha...")
            token = await self.turnstile.solve(page)
            if token:
                logger.info("Turnstile solved via 2Captcha")
                return True
            else:
                logger.warning("Failed to solve Turnstile via 2Captcha")
                return False

        except Exception as e:
            logger.error(f"Turnstile handling error: {e}")
            return False

    async def _is_otp_page(self, page: Page) -> bool:
        """Check if the OTP/2FA page is showing"""
        try:
            content = await page.content()
            otp_indicators = [
                "one time password",
                "otp",
                "verification code",
                "enter your code",
                "enviado por e-mail",
            ]

            content_lower = content.lower()
            for indicator in otp_indicators:
                if indicator in content_lower:
                    return True

            otp_input = await page.query_selector(Selectors.OTP_INPUT)
            if otp_input and await otp_input.is_visible():
                return True

            return False
        except:
            return False

    async def _handle_otp(self, page: Page) -> Tuple[bool, str]:
        """Handle OTP/2FA step (with Turnstile on OTP page)"""
        logger.info("Handling OTP verification...")
        await self.browser.screenshot("otp_page")

        # Method 1: Try to read OTP from email automatically
        # Wait a few seconds for the email to arrive
        logger.info("Waiting 10s for OTP email to arrive...")
        await asyncio.sleep(10)
        otp_code = await self._read_otp_from_email()

        # Method 2: Use callback if configured (e.g., Telegram prompt)
        if not otp_code and self._otp_callback:
            logger.info("Requesting OTP via callback...")
            try:
                otp_code = await self._otp_callback()
            except Exception as e:
                logger.error(f"OTP callback error: {e}")

        # Method 3: If no OTP from email, retry a few times
        if not otp_code:
            for retry in range(1, 6):  # Retry 5 times, 10s apart
                logger.info(f"OTP email not found, retrying ({retry}/5)...")
                await asyncio.sleep(10)
                otp_code = await self._read_otp_from_email()
                if otp_code:
                    break

        # Method 4: Wait for user to enter OTP manually
        if not otp_code:
            logger.info("Waiting for manual OTP entry (120s timeout)...")
            logger.info("Please enter the OTP code in the browser window")
            success = await self._wait_for_manual_otp(page, timeout=120)
            if success:
                return True, "OTP entered manually"
            return False, "OTP timeout - no code entered within 120 seconds"

        # Enter the OTP code
        logger.info("Entering OTP code...")
        try:
            # Find OTP input field
            otp_input = await page.wait_for_selector(
                f"{Selectors.OTP_INPUT}, input[type='password'], input[type='text']",
                timeout=10000
            )
            if otp_input:
                await otp_input.click()
                await self.browser.random_delay(200, 500)
                # Clear any existing value first
                await page.keyboard.press("Control+A")
                await page.keyboard.type(otp_code, delay=80)
                await self.browser.random_delay(500, 1000)

                await self.browser.screenshot("otp_entered")

                # Turnstile appears on OTP page too - wait and solve it
                logger.info("Checking for Turnstile on OTP page...")
                await self._wait_and_solve_turnstile(page, timeout=15)

                # Wait for Sign In button to be enabled
                await self.browser.random_delay(1000, 2000)

                # Click Sign In on OTP page
                sign_in_selectors = [
                    Selectors.OTP_SUBMIT,
                    "button:has-text('Sign In')",
                    "button:has-text('Sign in')",
                    "button[type='submit']",
                ]

                for selector in sign_in_selectors:
                    try:
                        btn = await page.query_selector(selector)
                        if btn:
                            await btn.click()
                            logger.info(f"OTP Sign In clicked with: {selector}")
                            break
                    except:
                        continue

                await self.browser.random_delay(3000, 5000)
                await self.browser.screenshot("after_otp_submit")

                return True, "OTP submitted"
            else:
                return False, "OTP input field not found"
        except Exception as e:
            logger.error(f"OTP entry error: {e}")
            return False, f"OTP entry error: {str(e)}"

    async def _read_otp_from_email(self) -> Optional[str]:
        """Try to read OTP code from email via IMAP"""
        if not settings.smtp_user or not settings.smtp_password:
            logger.info("Email not configured, skipping auto OTP read")
            return None

        try:
            import imaplib
            import email as email_lib
            from datetime import datetime, timedelta

            logger.info("Checking email for OTP code...")

            imap = imaplib.IMAP4_SSL("imap.gmail.com")
            imap.login(settings.smtp_user, settings.smtp_password)
            imap.select("INBOX")

            # Search for recent VFS emails (last 5 minutes)
            date_str = (datetime.now() - timedelta(minutes=5)).strftime("%d-%b-%Y")
            _, message_ids = imap.search(None, f'(SINCE "{date_str}" FROM "vfsglobal")')

            if not message_ids[0]:
                logger.info("No recent VFS emails found")
                imap.logout()
                return None

            # Get the latest email
            latest_id = message_ids[0].split()[-1]
            _, msg_data = imap.fetch(latest_id, "(RFC822)")

            msg = email_lib.message_from_bytes(msg_data[0][1])

            # Extract body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
                    elif part.get_content_type() == "text/html":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

            imap.logout()

            # Extract OTP code (typically 6 digits)
            otp_match = re.search(r'\b(\d{6})\b', body)
            if otp_match:
                otp = otp_match.group(1)
                logger.info(f"OTP code found in email: {otp[:2]}****")
                return otp

            # Try 4-digit OTP
            otp_match = re.search(r'\b(\d{4})\b', body)
            if otp_match:
                otp = otp_match.group(1)
                logger.info(f"OTP code found in email: {otp[:2]}**")
                return otp

            logger.info("Could not extract OTP from email body")
            return None

        except Exception as e:
            logger.error(f"Failed to read OTP from email: {e}")
            return None

    async def _wait_for_manual_otp(self, page: Page, timeout: int = 120) -> bool:
        """Wait for user to manually enter OTP in the browser"""
        try:
            await page.wait_for_function(
                """
                () => {
                    if (window.location.href.includes('/dashboard')) return true;
                    const body = document.body.innerText.toLowerCase();
                    if (!body.includes('one time password') && !body.includes('otp')) return true;
                    return false;
                }
                """,
                timeout=timeout * 1000,
            )
            return True
        except:
            return False

    async def _is_blocked_page(self, page: Page) -> bool:
        """Check if Cloudflare/VFS WAF blocked the request (403201 JSON response)"""
        try:
            content = await page.content()

            if '"403201"' in content or '"code":"403201"' in content.replace(" ", ""):
                return True

            content_lower = content.lower()
            if "access denied" in content_lower and "cloudflare" in content_lower:
                return True

            body_text = await page.evaluate("() => document.body?.innerText?.trim() || ''")
            if body_text and body_text.startswith('{"code"'):
                return True

            return False
        except:
            return False

    async def _is_session_expired_page(self, page: Page) -> bool:
        """Check if current page is the 'Session Expired' page"""
        try:
            url = page.url.lower()
            if "page-not-found" in url:
                return True

            content = await page.content()
            content_lower = content.lower()
            for indicator in ["session expired", "session invalid", "sessão expirada", "go back to home"]:
                if indicator in content_lower:
                    return True

            return False
        except:
            return False

    async def _clear_all_storage(self, page: Page):
        """Clear cookies, localStorage, and sessionStorage"""
        try:
            await page.context.clear_cookies()
            await page.evaluate("""
                () => {
                    try { localStorage.clear(); } catch(e) {}
                    try { sessionStorage.clear(); } catch(e) {}
                }
            """)

            session_file = self.browser._session_file
            if session_file.exists():
                import os
                os.remove(session_file)

            logger.info("Cleared all browser storage")
        except Exception as e:
            logger.debug(f"Storage cleanup: {e}")

    async def logout(self) -> bool:
        """Logout from VFS Global"""
        page = self.browser.page
        if not page:
            return False

        try:
            # From Screenshot 19: "Sign Out" is in the top right
            logout_selectors = [
                "a:has-text('Sign Out')",
                "button:has-text('Sign Out')",
                "a:has-text('Logout')",
                "button:has-text('Logout')",
            ]

            for selector in logout_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        await element.click()
                        await self.browser.wait_for_navigation()
                        logger.info("Logged out successfully")
                        return True
                except:
                    continue

            await page.context.clear_cookies()
            logger.info("Session cleared")
            return True

        except Exception as e:
            logger.error(f"Logout error: {e}")
            return False

    async def _handle_cookie_consent(self, page: Page):
        """Handle cookie consent popup - click 'Accept All Cookies'"""
        try:
            # Wait for cookie banner (from Screenshot 4: banner appears at bottom)
            try:
                await page.wait_for_selector(
                    "#onetrust-banner-sdk, #onetrust-consent-sdk, .onetrust-pc-dark-filter",
                    timeout=8000
                )
                logger.info("Cookie consent element detected")
            except:
                logger.debug("No cookie consent elements found")
                return

            # From Screenshot 5: "Accept All Cookies" button
            button_selectors = [
                "#onetrust-accept-btn-handler",
                "button:has-text('Accept All Cookies')",
                "button:has-text('Aceitar todos os cookies')",
                "button:has-text('Accept All')",
                "#accept-recommended-btn-handler",
            ]

            button_clicked = False
            for selector in button_selectors:
                try:
                    button = await page.query_selector(selector)
                    if button:
                        await button.click(force=True)
                        logger.info(f"Cookie consent handled with: {selector}")
                        await self.browser.random_delay(1000, 2000)
                        button_clicked = True
                        break
                except:
                    continue

            # Always remove blocking overlays via JS
            await self._remove_cookie_overlays(page)

            if not button_clicked:
                logger.warning("No cookie buttons matched, overlays removed via JS")

        except Exception as e:
            logger.debug(f"Cookie consent handling: {e}")

    async def _remove_cookie_overlays(self, page: Page):
        """Remove OneTrust cookie overlays that block clicks"""
        try:
            await page.evaluate("""
                () => {
                    document.querySelectorAll('.onetrust-pc-dark-filter').forEach(el => el.remove());
                    const sdk = document.getElementById('onetrust-consent-sdk');
                    if (sdk) sdk.style.display = 'none';
                    document.querySelectorAll('[class*="onetrust"][class*="filter"]').forEach(el => el.remove());
                }
            """)
            logger.debug("Cookie overlays removed")
        except Exception as e:
            logger.debug(f"Overlay removal: {e}")

    async def _is_logged_in(self, page: Page) -> bool:
        """Check if user is logged in (from Screenshot 19: /dashboard with 'Start New Booking')"""
        try:
            if "/dashboard" in page.url:
                return True

            dashboard_selectors = [
                Selectors.NEW_BOOKING_BUTTON,
                "text=Start New Booking",
                "text=Active application",
                "text=No Application(s) Found",
                "a:has-text('Sign Out')",
            ]

            for selector in dashboard_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        return True
                except:
                    continue

            return False
        except:
            return False

    async def _wait_for_login_result(self, page: Page, timeout: int = 30000):
        """Wait for login result (dashboard or error)"""
        try:
            await page.wait_for_function(
                """
                () => {
                    if (window.location.href.includes('/dashboard')) return true;
                    if (document.querySelector('.alert-danger')) return true;
                    if (document.querySelector('.error-message')) return true;
                    // Also check for OTP page (login step 2)
                    if (document.body.innerText.toLowerCase().includes('one time password')) return true;
                    return false;
                }
                """,
                timeout=timeout,
            )
        except:
            pass

        await self.browser.random_delay(1000, 2000)

    async def _get_error_message(self, page: Page) -> str:
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

            return "Unknown error"
        except:
            return "Unknown error"

    async def check_session(self) -> bool:
        """Check if current session is still valid"""
        page = self.browser.page
        if not page:
            return False

        try:
            await page.goto(VFSUrls.DASHBOARD, wait_until="domcontentloaded", timeout=30000)
            await self.browser.random_delay(1000, 2000)

            if "/login" in page.url:
                logger.info("Session expired, need to re-login")
                return False

            return await self._is_logged_in(page)

        except Exception as e:
            logger.error(f"Session check error: {e}")
            return False
