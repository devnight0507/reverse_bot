"""
Pydantic schemas for API request/response validation
"""
from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field


# ============== Applicant Schemas ==============

class ApplicantBase(BaseModel):
    """Base applicant schema"""
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    phone: str = Field(..., min_length=5, max_length=50)
    passport_number: str = Field(..., min_length=5, max_length=50)
    passport_expiry: date
    date_of_birth: date
    nationality: str = Field(default="Angola", max_length=50)
    gender: str = Field(default="Male", pattern="^(Male|Female)$")
    visa_type: str = Field(default="TOURIST", max_length=50)
    priority: int = Field(default=0, ge=0, le=100)
    notes: Optional[str] = None


class ApplicantCreate(ApplicantBase):
    """Schema for creating an applicant"""
    pass


class ApplicantUpdate(BaseModel):
    """Schema for updating an applicant"""
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, min_length=5, max_length=50)
    passport_number: Optional[str] = Field(None, min_length=5, max_length=50)
    passport_expiry: Optional[date] = None
    date_of_birth: Optional[date] = None
    nationality: Optional[str] = Field(None, max_length=50)
    gender: Optional[str] = Field(None, pattern="^(Male|Female)$")
    visa_type: Optional[str] = Field(None, max_length=50)
    priority: Optional[int] = Field(None, ge=0, le=100)
    status: Optional[str] = None
    notes: Optional[str] = None


class ApplicantResponse(ApplicantBase):
    """Schema for applicant response"""
    id: int
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== Booking Schemas ==============

class BookingBase(BaseModel):
    """Base booking schema"""
    center: str = Field(default="Luanda", max_length=100)
    category: str = Field(default="Short Stay", max_length=100)
    subcategory: str = Field(default="Tourism", max_length=100)


class BookingCreate(BookingBase):
    """Schema for creating a booking"""
    applicant_id: int


class BookingUpdate(BaseModel):
    """Schema for updating a booking"""
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    center: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    status: Optional[str] = None
    confirmation_code: Optional[str] = None
    error_message: Optional[str] = None


class BookingLogResponse(BaseModel):
    """Schema for booking log response"""
    id: int
    step: str
    status: str
    message: Optional[str]
    duration_ms: Optional[int]
    screenshot_path: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class BookingResponse(BookingBase):
    """Schema for booking response"""
    id: int
    applicant_id: int
    appointment_date: Optional[date]
    appointment_time: Optional[str]
    status: str
    confirmation_code: Optional[str]
    screenshot_path: Optional[str]
    attempts: int
    last_attempt: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    logs: List[BookingLogResponse] = []

    class Config:
        from_attributes = True


# ============== Bot Control Schemas ==============

class BotStartRequest(BaseModel):
    """Schema for starting the bot"""
    applicant_ids: Optional[List[int]] = None  # If None, process all pending
    monitor_mode: bool = Field(default=True, description="Continue monitoring after booking")


class BotStatusResponse(BaseModel):
    """Schema for bot status response"""
    is_running: bool
    current_applicant_id: Optional[int]
    current_step: Optional[str]
    total_processed: int
    total_success: int
    total_failed: int
    last_check: Optional[datetime]
    next_check: Optional[datetime]


class SlotCheckResponse(BaseModel):
    """Schema for slot availability response"""
    available: bool
    dates: List[date] = []
    message: str
    checked_at: datetime


# ============== Notification Schemas ==============

class NotificationTestRequest(BaseModel):
    """Schema for testing notifications"""
    type: str = Field(..., pattern="^(telegram|email)$")
    message: str = Field(default="Test notification from VFS Bot")


class NotificationResponse(BaseModel):
    """Schema for notification response"""
    success: bool
    message: str


# ============== Statistics Schemas ==============

class StatsResponse(BaseModel):
    """Schema for statistics response"""
    total_applicants: int
    pending_applicants: int
    booked_applicants: int
    failed_applicants: int
    total_bookings: int
    successful_bookings: int
    failed_bookings: int
    average_attempts: float
    last_successful_booking: Optional[datetime]
