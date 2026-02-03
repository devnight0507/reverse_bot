"""
CRUD operations for database models
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Applicant, Booking, BookingLog, Session, Settings


# ============== Applicant CRUD ==============

async def create_applicant(db: AsyncSession, **kwargs) -> Applicant:
    """Create a new applicant"""
    applicant = Applicant(**kwargs)
    db.add(applicant)
    await db.commit()
    await db.refresh(applicant)
    return applicant


async def get_applicant(db: AsyncSession, applicant_id: int) -> Optional[Applicant]:
    """Get applicant by ID"""
    result = await db.execute(
        select(Applicant).where(Applicant.id == applicant_id)
    )
    return result.scalar_one_or_none()


async def get_applicant_by_passport(db: AsyncSession, passport_number: str) -> Optional[Applicant]:
    """Get applicant by passport number"""
    result = await db.execute(
        select(Applicant).where(Applicant.passport_number == passport_number)
    )
    return result.scalar_one_or_none()


async def get_applicants(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None
) -> List[Applicant]:
    """Get list of applicants"""
    query = select(Applicant).order_by(Applicant.priority.desc(), Applicant.created_at)

    if status:
        query = query.where(Applicant.status == status)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def update_applicant(db: AsyncSession, applicant_id: int, **kwargs) -> Optional[Applicant]:
    """Update an applicant"""
    await db.execute(
        update(Applicant)
        .where(Applicant.id == applicant_id)
        .values(**kwargs, updated_at=datetime.utcnow())
    )
    await db.commit()
    return await get_applicant(db, applicant_id)


async def delete_applicant(db: AsyncSession, applicant_id: int) -> bool:
    """Delete an applicant"""
    result = await db.execute(
        delete(Applicant).where(Applicant.id == applicant_id)
    )
    await db.commit()
    return result.rowcount > 0


async def count_applicants(db: AsyncSession, status: Optional[str] = None) -> int:
    """Count applicants"""
    query = select(func.count(Applicant.id))
    if status:
        query = query.where(Applicant.status == status)
    result = await db.execute(query)
    return result.scalar() or 0


# ============== Booking CRUD ==============

async def create_booking(db: AsyncSession, **kwargs) -> Booking:
    """Create a new booking"""
    booking = Booking(**kwargs)
    db.add(booking)
    await db.commit()
    await db.refresh(booking)
    return booking


async def get_booking(db: AsyncSession, booking_id: int) -> Optional[Booking]:
    """Get booking by ID with logs"""
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.logs))
        .where(Booking.id == booking_id)
    )
    return result.scalar_one_or_none()


async def get_bookings(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    applicant_id: Optional[int] = None,
    status: Optional[str] = None
) -> List[Booking]:
    """Get list of bookings"""
    query = select(Booking).options(selectinload(Booking.logs)).order_by(Booking.created_at.desc())

    if applicant_id:
        query = query.where(Booking.applicant_id == applicant_id)
    if status:
        query = query.where(Booking.status == status)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def update_booking(db: AsyncSession, booking_id: int, **kwargs) -> Optional[Booking]:
    """Update a booking"""
    await db.execute(
        update(Booking)
        .where(Booking.id == booking_id)
        .values(**kwargs, updated_at=datetime.utcnow())
    )
    await db.commit()
    return await get_booking(db, booking_id)


async def delete_booking(db: AsyncSession, booking_id: int) -> bool:
    """Delete a booking"""
    result = await db.execute(
        delete(Booking).where(Booking.id == booking_id)
    )
    await db.commit()
    return result.rowcount > 0


async def increment_booking_attempts(db: AsyncSession, booking_id: int) -> Optional[Booking]:
    """Increment booking attempts counter"""
    await db.execute(
        update(Booking)
        .where(Booking.id == booking_id)
        .values(
            attempts=Booking.attempts + 1,
            last_attempt=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
    )
    await db.commit()
    return await get_booking(db, booking_id)


async def count_bookings(db: AsyncSession, status: Optional[str] = None) -> int:
    """Count bookings"""
    query = select(func.count(Booking.id))
    if status:
        query = query.where(Booking.status == status)
    result = await db.execute(query)
    return result.scalar() or 0


# ============== Booking Log CRUD ==============

async def create_booking_log(db: AsyncSession, **kwargs) -> BookingLog:
    """Create a new booking log entry"""
    log = BookingLog(**kwargs)
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def get_booking_logs(db: AsyncSession, booking_id: int) -> List[BookingLog]:
    """Get all logs for a booking"""
    result = await db.execute(
        select(BookingLog)
        .where(BookingLog.booking_id == booking_id)
        .order_by(BookingLog.created_at)
    )
    return list(result.scalars().all())


# ============== Session CRUD ==============

async def save_session(db: AsyncSession, name: str, cookies: str, **kwargs) -> Session:
    """Save or update browser session"""
    result = await db.execute(
        select(Session).where(Session.name == name)
    )
    session = result.scalar_one_or_none()

    if session:
        await db.execute(
            update(Session)
            .where(Session.name == name)
            .values(cookies=cookies, **kwargs, updated_at=datetime.utcnow())
        )
        await db.commit()
        result = await db.execute(select(Session).where(Session.name == name))
        return result.scalar_one()
    else:
        session = Session(name=name, cookies=cookies, **kwargs)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session


async def get_session(db: AsyncSession, name: str = "default") -> Optional[Session]:
    """Get browser session by name"""
    result = await db.execute(
        select(Session).where(Session.name == name, Session.is_active == True)
    )
    return result.scalar_one_or_none()


async def invalidate_session(db: AsyncSession, name: str = "default") -> bool:
    """Invalidate a session"""
    result = await db.execute(
        update(Session)
        .where(Session.name == name)
        .values(is_active=False, updated_at=datetime.utcnow())
    )
    await db.commit()
    return result.rowcount > 0


# ============== Settings CRUD ==============

async def get_setting(db: AsyncSession, key: str) -> Optional[str]:
    """Get a setting value"""
    result = await db.execute(
        select(Settings).where(Settings.key == key)
    )
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_setting(db: AsyncSession, key: str, value: str, description: str = None) -> Settings:
    """Set a setting value"""
    result = await db.execute(
        select(Settings).where(Settings.key == key)
    )
    setting = result.scalar_one_or_none()

    if setting:
        await db.execute(
            update(Settings)
            .where(Settings.key == key)
            .values(value=value, updated_at=datetime.utcnow())
        )
        await db.commit()
        result = await db.execute(select(Settings).where(Settings.key == key))
        return result.scalar_one()
    else:
        setting = Settings(key=key, value=value, description=description)
        db.add(setting)
        await db.commit()
        await db.refresh(setting)
        return setting


# ============== Statistics ==============

async def get_statistics(db: AsyncSession) -> dict:
    """Get overall statistics"""
    total_applicants = await count_applicants(db)
    pending_applicants = await count_applicants(db, status="pending")
    booked_applicants = await count_applicants(db, status="booked")
    failed_applicants = await count_applicants(db, status="failed")

    total_bookings = await count_bookings(db)
    successful_bookings = await count_bookings(db, status="success")
    failed_bookings = await count_bookings(db, status="failed")

    # Average attempts
    result = await db.execute(
        select(func.avg(Booking.attempts)).where(Booking.status == "success")
    )
    avg_attempts = result.scalar() or 0

    # Last successful booking
    result = await db.execute(
        select(Booking.updated_at)
        .where(Booking.status == "success")
        .order_by(Booking.updated_at.desc())
        .limit(1)
    )
    last_success = result.scalar_one_or_none()

    return {
        "total_applicants": total_applicants,
        "pending_applicants": pending_applicants,
        "booked_applicants": booked_applicants,
        "failed_applicants": failed_applicants,
        "total_bookings": total_bookings,
        "successful_bookings": successful_bookings,
        "failed_bookings": failed_bookings,
        "average_attempts": round(avg_attempts, 2),
        "last_successful_booking": last_success,
    }
