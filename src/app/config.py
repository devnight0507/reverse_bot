"""
Configuration settings for VFS Booking Bot
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # VFS Global Credentials
    vfs_email: str = Field(default="", description="VFS Global login email")
    vfs_password: str = Field(default="", description="VFS Global login password")

    # 2Captcha API
    captcha_api_key: str = Field(default="", description="2Captcha API key for Turnstile")

    # Telegram Notifications
    telegram_bot_token: Optional[str] = Field(default=None, description="Telegram bot token")
    telegram_chat_id: Optional[str] = Field(default=None, description="Telegram chat ID")

    # Email Notifications
    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP server host")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: Optional[str] = Field(default=None, description="SMTP username")
    smtp_password: Optional[str] = Field(default=None, description="SMTP password")

    # Bot Settings
    monitor_interval: int = Field(default=30, description="Slot check interval in seconds")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    headless: bool = Field(default=False, description="Run browser in headless mode")
    screenshot_on_error: bool = Field(default=True, description="Capture screenshot on error")

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/vfs_bot.db",
        description="Database connection URL"
    )

    # API Server
    api_host: str = Field(default="127.0.0.1", description="API server host")
    api_port: int = Field(default=8000, description="API server port")

    # Paths
    base_dir: Path = Field(default=Path(__file__).parent.parent.parent, description="Base directory")

    @property
    def screenshots_dir(self) -> Path:
        return self.base_dir / "data" / "screenshots"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "data" / "logs"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# VFS Global URLs
class VFSUrls:
    """VFS Global Portugal (Angola) URLs"""
    BASE = "https://visa.vfsglobal.com/ago/en/prt"
    LOGIN = f"{BASE}/login"
    APPLICATION_DETAIL = f"{BASE}/application-detail"
    DASHBOARD = f"{BASE}/dashboard"
    BOOK_APPOINTMENT = f"{BASE}/book-an-appointment"
    YOUR_DETAILS = f"{BASE}/your-details"
    BOOK_APPOINTMENT_PAGE = f"{BASE}/book-appointment"
    REVIEW_PAY = f"{BASE}/review-pay"
    CONFIRMATION = f"{BASE}/confirmation"
    IDENTITY_VERIFICATION = "https://idnvui.vfsglobal.com/home"


# CSS Selectors for VFS Global site
class Selectors:
    """CSS Selectors for VFS Global site elements"""

    # Login Page
    EMAIL_INPUT = "#mat-input-0"
    PASSWORD_INPUT = "#mat-input-1"
    SIGN_IN_BUTTON = "button[type='submit']"

    # OTP (2FA) Page
    OTP_INPUT = "input[formcontrolname='otp'], input[type='text'][placeholder*='OTP'], input[type='password']"
    OTP_SUBMIT = "button[type='submit']"
    OTP_HEADING = "text=one time password"

    # Cookie Consent
    COOKIE_REJECT = "#onetrust-reject-all-handler"

    # Dashboard
    NEW_BOOKING_BUTTON = "button:has-text('Start New Booking')"
    SCHEDULE_NOW_BUTTON = "button:has-text('Schedule Now')"

    # Booking Form - Center Selection
    CENTER_SELECT = "mat-select[formcontrolname='centerCode']"
    CENTER_OPTION = "mat-option"

    # Booking Form - Category Selection
    CATEGORY_SELECT = "mat-select[formcontrolname='category']"
    SUBCATEGORY_SELECT = "mat-select[formcontrolname='subCategory']"

    # Booking Form - Payment Mode
    PAYMENT_MODE_SELECT = "mat-select[formcontrolname='paymentMode'], mat-select[formcontrolname='payment']"

    # Booking Form - Applicant Details
    FIRST_NAME = "input[formcontrolname='firstName']"
    LAST_NAME = "input[formcontrolname='lastName']"
    GENDER_SELECT = "mat-select[formcontrolname='gender']"
    DOB_INPUT = "input[formcontrolname='dateOfBirth']"
    NATIONALITY_SELECT = "mat-select[formcontrolname='nationality']"
    PASSPORT_NUMBER = "input[formcontrolname='passportNumber']"
    PASSPORT_EXPIRY = "input[formcontrolname='passportExpiryDate']"
    PHONE_CODE = "mat-select[formcontrolname='dialCode']"
    PHONE_NUMBER = "input[formcontrolname='mobileNumber']"
    EMAIL = "input[formcontrolname='emailId']"
    CONFIRM_EMAIL = "input[formcontrolname='confirmEmailId']"

    # Slot Availability (application-detail page)
    NO_SLOTS_MESSAGE = ".alert.alert-info, .alert-info, .info-alert"
    EARLIEST_SLOT_TEXT = "text=Earliest available slot"
    CONTINUE_BUTTON = "button:has-text('Continue')"

    # Calendar / Slot Selection (book-appointment page)
    APPOINTMENT_TYPE_SELECT = "mat-select, select"
    AVAILABLE_DATE = "td:not(.disabled):not(.unavailable) .day, td.available, .date-available, td:not([class*='disabled']):not([class*='unavailable'])"
    TIME_SLOT_SELECT = "button:has-text('Select')"
    LOAD_MORE = "a:has-text('Load More'), button:has-text('Load More')"
    CALENDAR_NEXT = "button[aria-label='Next month'], button:has-text('>')"

    # Review & Payment (review-pay page)
    TERMS_CHECKBOX_1 = "mat-checkbox:has-text('Terms and Conditions'), mat-checkbox:first-of-type"
    TERMS_CHECKBOX_2 = "mat-checkbox:has-text('communication'), mat-checkbox:has-text('agree'), mat-checkbox:last-of-type"
    PAYMENT_BUTTON = "button:has-text('Multicaixa'), button:has-text('Pay'), button:has-text('Confirm')"

    # Booking OTP (your-details page, after identity verification)
    GENERATE_OTP_BUTTON = "button:has-text('Generate OTP'), button:has-text('Generate')"
    BOOKING_OTP_INPUT = "input[formcontrolname='otp'], input[placeholder*='OTP'], input[type='text']"
    VERIFY_OTP_BUTTON = "button:has-text('Verify')"

    # Identity Verification Status
    VERIFICATION_PENDING = "text=Verification pending"
    VERIFICATION_PASSED = "text=Verification Passed"

    # Modals & Buttons
    SAVE_BUTTON = "button:has-text('Save')"
    GO_BACK_BUTTON = "button:has-text('Go Back')"

    # Status Messages
    SUCCESS_MESSAGE = ".alert.alert-success, .alert-success"
    ERROR_MESSAGE = ".alert.alert-danger, .alert-danger"

    # Turnstile
    TURNSTILE_IFRAME = "iframe[src*='challenges.cloudflare.com']"
    TURNSTILE_WIDGET = ".cf-turnstile, [data-sitekey]"
    TURNSTILE_CHECKBOX = "#challenge-stage"

    # Loading
    SPINNER = ".mat-progress-spinner, .mat-spinner"
    LOADING = ".loading, .loader"


# Visa Categories (actual VFS Angola values from screenshots)
VISA_CATEGORIES = {
    "Job Seeker": ["Job seekers"],
    "Visto Nacional": ["Visto Nacional (National visa)"],
    "Visto Schengen": ["Visto Schengen (Schengen Visa)"],
}

# Applicant Status
APPLICANT_STATUS = [
    "pending",
    "in_queue",
    "processing",
    "booked",
    "failed",
]

# Booking Status
BOOKING_STATUS = [
    "pending",
    "monitoring",
    "slot_found",
    "booking",
    "success",
    "failed",
    "cancelled",
]


# Global settings instance
settings = Settings()
