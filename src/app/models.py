"""
Database models for VFS Booking Bot
"""
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import String, Integer, DateTime, Date, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class Applicant(Base):
    """Applicant model - stores visa applicant information"""
    __tablename__ = "applicants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    passport_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    passport_expiry: Mapped[date] = mapped_column(Date, nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    nationality: Mapped[str] = mapped_column(String(50), default="Angola")
    gender: Mapped[str] = mapped_column(String(10), default="Male")  # Male/Female
    visa_type: Mapped[str] = mapped_column(String(50), default="TOURIST")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    bookings: Mapped[List["Booking"]] = relationship("Booking", back_populates="applicant", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Applicant {self.first_name} {self.last_name} ({self.passport_number})>"


class Booking(Base):
    """Booking model - stores booking attempts and results"""
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(Integer, ForeignKey("applicants.id"), nullable=False)
    appointment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    appointment_time: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    center: Mapped[str] = mapped_column(String(100), default="Luanda")
    category: Mapped[str] = mapped_column(String(100), default="Short Stay")
    subcategory: Mapped[str] = mapped_column(String(100), default="Tourism")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    confirmation_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    applicant: Mapped["Applicant"] = relationship("Applicant", back_populates="bookings")
    logs: Mapped[List["BookingLog"]] = relationship("BookingLog", back_populates="booking", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Booking {self.id} for Applicant {self.applicant_id} - {self.status}>"


class BookingLog(Base):
    """Booking log model - stores detailed logs for each booking step"""
    __tablename__ = "booking_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(Integer, ForeignKey("bookings.id"), nullable=False)
    step: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # success/failed/skipped
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    booking: Mapped["Booking"] = relationship("Booking", back_populates="logs")

    def __repr__(self):
        return f"<BookingLog {self.step} - {self.status}>"


class Session(Base):
    """Session model - stores browser session data for persistence"""
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), default="default")
    cookies: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    local_storage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Session {self.name} - Active: {self.is_active}>"


class Settings(Base):
    """Settings model - stores application configuration"""
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Settings {self.key}>"
