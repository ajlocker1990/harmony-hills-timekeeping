import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import psycopg
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from discord_admin import router as discord_admin_router
from discord_admin import register_commands


# ============================================================
# HARMONY HILLS TIMEKEEPING
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")
CLOCK_API_KEY = os.getenv("CLOCK_API_KEY")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")

HHFD_DISCORD_WEBHOOK = os.getenv(
    "HHFD_DISCORD_WEBHOOK"
)

SYSTEM_TIMEZONE = os.getenv(
    "SYSTEM_TIMEZONE",
    "America/Los_Angeles",
)


if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not configured."
    )

if not CLOCK_API_KEY:
    raise RuntimeError(
        "CLOCK_API_KEY environment variable is not configured."
    )

if not ADMIN_API_KEY:
    raise RuntimeError(
        "ADMIN_API_KEY environment variable is not configured."
    )


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Harmony Hills Timekeeping",
    description="Harmony Hills personnel timekeeping system.",
    version="0.4.1",
)


# ============================================================
# DISCORD PRIVATE ADMIN ROUTER
# ============================================================

app.include_router(
    discord_admin_router
)


@app.on_event("startup")
async def startup_discord():
    await register_commands()


# ============================================================
# REQUEST MODELS
# ============================================================

class ClockInRequest(BaseModel):
    avatar_uuid: UUID

    avatar_name: str = Field(
        min_length=1,
        max_length=200,
    )

    group_uuid: UUID
    clock_uuid: UUID

    clock_name: Optional[str] = Field(
        default=None,
        max_length=200,
    )


class ClockOutRequest(BaseModel):
    avatar_uuid: UUID

    avatar_name: str = Field(
        min_length=1,
        max_length=200,
    )

    group_uuid: UUID
    clock_uuid: UUID

    clock_name: Optional[str] = Field(
        default=None,
        max_length=200,
    )

    activities: str = Field(
        min_length=1,
        max_length=1500,
    )


class AdjustmentRequestCreate(BaseModel):
    avatar_uuid: UUID

    avatar_name: str = Field(
        min_length=1,
        max_length=200,
    )

    group_uuid: UUID
    clock_uuid: UUID

    clock_name: Optional[str] = Field(
        default=None,
        max_length=200,
    )

    request_type: str = Field(
        min_length=1,
        max_length=30,
    )

    requested_clock_in: Optional[datetime] = None
    requested_clock_out: Optional[datetime] = None

    reason: str = Field(
        min_length=1,
        max_length=1000,
    )


class AdminAdjustmentReviewRequest(BaseModel):
    reviewed_by: str = Field(
        min_length=1,
        max_length=200,
    )

    review_notes: Optional[str] = Field(
        default=None,
        max_length=1000,
    )


class AdminShiftEditRequest(BaseModel):
    clock_in: Optional[datetime] = None
    clock_out: Optional[datetime] = None

    activities: Optional[str] = Field(
        default=None,
        max_length=1500,
    )

    reason: str = Field(
        min_length=1,
        max_length=500,
    )

    changed_by: str = Field(
        min_length=1,
        max_length=200,
    )


class AdminShiftAddRequest(BaseModel):
    avatar_uuid: UUID

    avatar_name: str = Field(
        min_length=1,
        max_length=200,
    )

    department_code: str = Field(
        min_length=1,
        max_length=20,
    )

    clock_in: datetime
    clock_out: datetime

    activities: Optional[str] = Field(
        default=None,
        max_length=1500,
    )

    reason: str = Field(
        min_length=1,
        max_length=500,
    )

    changed_by: str = Field(
        min_length=1,
        max_length=200,
    )


class AdminShiftVoidRequest(BaseModel):
    reason: str = Field(
        min_length=1,
        max_length=500,
    )

    changed_by: str = Field(
        min_length=1,
        max_length=200,
    )


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    return psycopg.connect(
        DATABASE_URL,
        autocommit=False,
    )


# ============================================================
# AUTH
# ============================================================

def verify_clock_api_key(
    api_key: Optional[str],
):
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing clock API key.",
        )

    if api_key != CLOCK_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Invalid clock API key.",
        )


def verify_admin_api_key(
    api_key: Optional[str],
):
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing admin API key.",
        )

    if api_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Invalid admin API key.",
        )


# ============================================================
# TIME HELPERS
# ============================================================

def ensure_timezone(
    value: datetime,
) -> datetime:
    if value.tzinfo is not None:
        return value

    return value.replace(
        tzinfo=ZoneInfo(
            SYSTEM_TIMEZONE
        )
    )


def format_duration(
    seconds: int,
):
    seconds = max(
        0,
        int(seconds),
    )

    hours = seconds // 3600

    minutes = (
        seconds % 3600
    ) // 60

    return {
        "seconds": seconds,
        "hours": hours,
        "minutes": minutes,
        "display": f"{hours}h {minutes}m",
    }


def discord_timestamp(
    value: datetime,
):
    return int(
        value.timestamp()
    )


# ============================================================
# DISCORD WEBHOOK OUTPUT
# ============================================================

def send_department_discord(
    department_code: str,
    message: str,
):
    webhook = None

    if department_code == "HHFD":
        webhook = HHFD_DISCORD_WEBHOOK

    if not webhook:
        return False

    payload = {
        "username": "Harmony Hills Timekeeping",

        "content": message,

        "allowed_mentions": {
            "parse": []
        },
    }

    try:
        with httpx.Client(
            timeout=10.0
        ) as client:

            response = client.post(
                webhook,
                json=payload,
            )

            response.raise_for_status()

        return True

    except Exception as exc:
        print(
            "Discord webhook notification failed:",
            exc,
        )

        return False


# ============================================================
# DEPARTMENT LOOKUP
# ============================================================

def get_department_by_group(
    cur,
    group_uuid: UUID,
):
    cur.execute(
        """
        select
            id,
            code,
            name,
            active
        from public.timekeeping_departments
        where sl_group_uuid = %s
        limit 1
        """,
        (
            str(group_uuid),
        ),
    )

    row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=403,
            detail=(
                "This Second Life group is not registered "
                "to a Harmony Hills department."
            ),
        )

    if not row[3]:
        raise HTTPException(
            status_code=403,
            detail="This department is inactive.",
        )

    return {
        "id": row[0],
        "code": row[1],
        "name": row[2],
    }


def get_department_by_code(
    cur,
    department_code: str,
):
    cur.execute(
        """
        select
            id,
            code,
            name,
            active
        from public.timekeeping_departments
        where upper(code) = upper(%s)
        limit 1
        """,
        (
            department_code,
        ),
    )

    row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Department not found.",
        )

    if not row[3]:
        raise HTTPException(
            status_code=403,
            detail="Department is inactive.",
        )

    return {
        "id": row[0],
        "code": row[1],
        "name": row[2],
    }


# ============================================================
# CLOCK REGISTRATION
# ============================================================

def validate_or_register_clock(
    cur,
    clock_uuid: UUID,
    clock_name: Optional[str],
    department_id,
):
    cur.execute(
        """
        select
            id,
            department_id,
            active
        from public.timekeeping_clocks
        where object_uuid = %s
        limit 1
        """,
        (
            str(clock_uuid),
        ),
    )

    row = cur.fetchone()

    if row:
        if not row[2]:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This Harmony Hills Time Clock "
                    "has been disabled."
                ),
            )

        if row[1] != department_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This time clock is registered "
                    "to another department."
                ),
            )

        cur.execute(
            """
            update public.timekeeping_clocks
            set
                object_name = %s,
                last_seen_at = now()
            where id = %s
            """,
            (
                clock_name,
                row[0],
            ),
        )

        return row[0]

    cur.execute(
        """
        insert into public.timekeeping_clocks (
            object_uuid,
            object_name,
            department_id,
            active,
            last_seen_at
        )
        values (
            %s,
            %s,
            %s,
            true,
            now()
        )
        returning id
        """,
        (
            str(clock_uuid),
            clock_name,
            department_id,
        ),
    )

    return cur.fetchone()[0]


# ============================================================
# EMPLOYEES
# ============================================================

def get_or_create_employee(
    cur,
    avatar_uuid: UUID,
    avatar_name: str,
):
    cur.execute(
        """
        select
            id,
            active
        from public.timekeeping_employees
        where avatar_uuid = %s
        limit 1
        """,
        (
            str(avatar_uuid),
        ),
    )

    row = cur.fetchone()

    if row:
        if not row[1]:
            raise HTTPException(
                status_code=403,
                detail="This employee is inactive.",
            )

        cur.execute(
            """
            update public.timekeeping_employees
            set avatar_name = %s
            where id = %s
            """,
            (
                avatar_name,
                row[0],
            ),
        )

        return row[0]

    cur.execute(
        """
        insert into public.timekeeping_employees (
            avatar_uuid,
            avatar_name,
            active
        )
        values (
            %s,
            %s,
            true
        )
        returning id
        """,
        (
            str(avatar_uuid),
            avatar_name,
        ),
    )

    return cur.fetchone()[0]


# ============================================================
# MEMBERSHIP
# ============================================================

def ensure_membership(
    cur,
    employee_id,
    department_id,
):
    cur.execute(
        """
        select
            id,
            active
        from public.timekeeping_memberships
        where employee_id = %s
        and department_id = %s
        limit 1
        """,
        (
            employee_id,
            department_id,
        ),
    )

    row = cur.fetchone()

    if row:
        if not row[1]:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Your membership in this department "
                    "is inactive."
                ),
            )

        return row[0]

    cur.execute(
        """
        insert into public.timekeeping_memberships (
            employee_id,
            department_id,
            active
        )
        values (
            %s,
            %s,
            true
        )
        returning id
        """,
        (
            employee_id,
            department_id,
        ),
    )

    return cur.fetchone()[0]


# ============================================================
# SHIFT HELPERS
# ============================================================

def get_latest_shift(
    cur,
    employee_id,
    department_id,
):
    cur.execute(
        """
        select
            id,
            clock_in,
            clock_out,
            activities,
            status
        from public.timekeeping_shifts
        where employee_id = %s
        and department_id = %s
        and status <> 'VOID'
        order by clock_in desc
        limit 1
        """,
        (
            employee_id,
            department_id,
        ),
    )

    return cur.fetchone()


def get_open_shift(
    cur,
    employee_id,
    department_id,
):
    cur.execute(
        """
        select
            id,
            clock_in,
            clock_out,
            activities,
            status
        from public.timekeeping_shifts
        where employee_id = %s
        and department_id = %s
        and status = 'OPEN'
        order by clock_in desc
        limit 1
        """,
        (
            employee_id,
            department_id,
        ),
    )

    return cur.fetchone()


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select 1;"
                )

                cur.fetchone()

        return {
            "status": "ok",
            "service": "Harmony Hills Timekeeping",
            "version": "0.4.1",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Database connection failed: {exc}"
            ),
        )


# ============================================================
# CLOCK STATUS
# ============================================================

@app.get(
    "/api/timekeeping/status/{avatar_uuid}"
)
def clock_status(
    avatar_uuid: UUID,
    group_uuid: UUID,
    x_api_key: Optional[str] = Header(
        default=None
    ),
):
    verify_clock_api_key(
        x_api_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_group(
                        cur,
                        group_uuid,
                    )
                )

                cur.execute(
                    """
                    select
                        id,
                        avatar_name
                    from public.timekeeping_employees
                    where avatar_uuid = %s
                    limit 1
                    """,
                    (
                        str(avatar_uuid),
                    ),
                )

                employee = cur.fetchone()

                if not employee:
                    return {
                        "success": True,
                        "clocked_in": False,

                        "department": {
                            "code": department["code"],
                            "name": department["name"],
                        },
                    }

                shift = get_open_shift(
                    cur,
                    employee[0],
                    department["id"],
                )

                if not shift:
                    return {
                        "success": True,
                        "clocked_in": False,

                        "department": {
                            "code": department["code"],
                            "name": department["name"],
                        },
                    }

                seconds = int(
                    (
                        datetime.now(
                            timezone.utc
                        )
                        - shift[1]
                    ).total_seconds()
                )

                return {
                    "success": True,
                    "clocked_in": True,

                    "shift_id": str(
                        shift[0]
                    ),

                    "clock_in": (
                        shift[1].isoformat()
                    ),

                    "duration": (
                        format_duration(
                            seconds
                        )
                    ),

                    "department": {
                        "code": department["code"],
                        "name": department["name"],
                    },
                }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to check clock status: {exc}"
            ),
        )


# ============================================================
# CLOCK IN
# ============================================================

@app.post(
    "/api/timekeeping/clock-in"
)
def clock_in(
    request: ClockInRequest,
    x_api_key: Optional[str] = Header(
        default=None
    ),
):
    verify_clock_api_key(
        x_api_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_group(
                        cur,
                        request.group_uuid,
                    )
                )

                validate_or_register_clock(
                    cur,
                    request.clock_uuid,
                    request.clock_name,
                    department["id"],
                )

                employee_id = (
                    get_or_create_employee(
                        cur,
                        request.avatar_uuid,
                        request.avatar_name,
                    )
                )

                ensure_membership(
                    cur,
                    employee_id,
                    department["id"],
                )

                existing = get_open_shift(
                    cur,
                    employee_id,
                    department["id"],
                )

                if existing:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "You are already clocked in."
                        ),
                    )

                clock_in_time = datetime.now(
                    timezone.utc
                )

                cur.execute(
                    """
                    insert into public.timekeeping_shifts (
                        employee_id,
                        department_id,
                        clock_in,
                        status,
                        source
                    )
                    values (
                        %s,
                        %s,
                        %s,
                        'OPEN',
                        'SL_CLOCK'
                    )
                    returning id
                    """,
                    (
                        employee_id,
                        department["id"],
                        clock_in_time,
                    ),
                )

                shift_id = (
                    cur.fetchone()[0]
                )

                conn.commit()

        send_department_discord(
            department["code"],
            (
                "🟢 **CLOCK IN**\n"
                f"**Member:** {request.avatar_name}\n"
                f"**Department:** {department['name']}\n"
                f"**Time:** <t:{discord_timestamp(clock_in_time)}:F>\n"
                "**Status:** ON DUTY"
            ),
        )

        return {
            "success": True,
            "action": "CLOCK_IN",

            "shift_id": str(
                shift_id
            ),

            "department": {
                "code": department["code"],
                "name": department["name"],
            },

            "clock_in": (
                clock_in_time.isoformat()
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Clock-in failed: {exc}"
            ),
        )


# ============================================================
# CLOCK OUT
# ============================================================

@app.post(
    "/api/timekeeping/clock-out"
)
def clock_out(
    request: ClockOutRequest,
    x_api_key: Optional[str] = Header(
        default=None
    ),
):
    verify_clock_api_key(
        x_api_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_group(
                        cur,
                        request.group_uuid,
                    )
                )

                validate_or_register_clock(
                    cur,
                    request.clock_uuid,
                    request.clock_name,
                    department["id"],
                )

                employee_id = (
                    get_or_create_employee(
                        cur,
                        request.avatar_uuid,
                        request.avatar_name,
                    )
                )

                ensure_membership(
                    cur,
                    employee_id,
                    department["id"],
                )

                shift = get_open_shift(
                    cur,
                    employee_id,
                    department["id"],
                )

                if not shift:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "No open shift was found "
                            "for this employee."
                        ),
                    )

                activities = (
                    request.activities.strip()
                )

                if not activities:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Shift activities are required."
                        ),
                    )

                clock_out_time = datetime.now(
                    timezone.utc
                )

                cur.execute(
                    """
                    update public.timekeeping_shifts
                    set
                        clock_out = %s,
                        activities = %s,
                        status = 'CLOSED'
                    where id = %s
                    """,
                    (
                        clock_out_time,
                        activities,
                        shift[0],
                    ),
                )

                conn.commit()

                total_seconds = int(
                    (
                        clock_out_time
                        - shift[1]
                    ).total_seconds()
                )

        duration = format_duration(
            total_seconds
        )

        send_department_discord(
            department["code"],
            (
                "📋 **COMPLETED SHIFT REPORT**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"**Member:** {request.avatar_name}\n"
                f"**Department:** {department['name']}\n\n"
                "🟢 **CLOCK IN**\n"
                f"<t:{discord_timestamp(shift[1])}:F>\n\n"
                "🔴 **CLOCK OUT**\n"
                f"<t:{discord_timestamp(clock_out_time)}:F>\n\n"
                "⏱️ **TOTAL SHIFT TIME**\n"
                f"{duration['display']}\n\n"
                "📝 **SHIFT ACTIVITIES**\n"
                f"{activities}"
            ),
        )

        return {
            "success": True,
            "action": "CLOCK_OUT",

            "shift_id": str(
                shift[0]
            ),

            "department": {
                "code": department["code"],
                "name": department["name"],
            },

            "clock_in": (
                shift[1].isoformat()
            ),

            "clock_out": (
                clock_out_time.isoformat()
            ),

            "duration": duration,

            "activities": activities,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Clock-out failed: {exc}"
            ),
        )


# ============================================================
# EMPLOYEE - CREATE ADJUSTMENT REQUEST
# ============================================================

@app.post(
    "/api/timekeeping/adjustments"
)
def create_adjustment_request(
    request: AdjustmentRequestCreate,
    x_api_key: Optional[str] = Header(
        default=None
    ),
):
    verify_clock_api_key(
        x_api_key
    )

    request_type = (
        request.request_type
        .strip()
        .upper()
    )

    if request_type not in {
        "CLOCK_IN",
        "CLOCK_OUT",
        "MISSED_SHIFT",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid adjustment request type."
            ),
        )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_group(
                        cur,
                        request.group_uuid,
                    )
                )

                validate_or_register_clock(
                    cur,
                    request.clock_uuid,
                    request.clock_name,
                    department["id"],
                )

                employee_id = (
                    get_or_create_employee(
                        cur,
                        request.avatar_uuid,
                        request.avatar_name,
                    )
                )

                ensure_membership(
                    cur,
                    employee_id,
                    department["id"],
                )

                requested_clock_in = (
                    ensure_timezone(
                        request.requested_clock_in
                    )
                    if request.requested_clock_in
                    else None
                )

                requested_clock_out = (
                    ensure_timezone(
                        request.requested_clock_out
                    )
                    if request.requested_clock_out
                    else None
                )

                shift_id = None

                existing_clock_in = None
                existing_clock_out = None

                if request_type == "MISSED_SHIFT":

                    if (
                        not requested_clock_in
                        or not requested_clock_out
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "Missed shift requests require "
                                "both clock-in and clock-out."
                            ),
                        )

                    if (
                        requested_clock_out
                        <= requested_clock_in
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "Clock-out must be after clock-in."
                            ),
                        )

                else:
                    shift = get_latest_shift(
                        cur,
                        employee_id,
                        department["id"],
                    )

                    if not shift:
                        raise HTTPException(
                            status_code=404,
                            detail=(
                                "No existing shift was found "
                                "to correct."
                            ),
                        )

                    shift_id = shift[0]

                    existing_clock_in = shift[1]
                    existing_clock_out = shift[2]

                    if (
                        request_type == "CLOCK_IN"
                        and not requested_clock_in
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "A corrected clock-in "
                                "time is required."
                            ),
                        )

                    if (
                        request_type == "CLOCK_OUT"
                        and not requested_clock_out
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "A corrected clock-out "
                                "time is required."
                            ),
                        )

                    cur.execute(
                        """
                        select id
                        from public.timekeeping_adjustment_requests
                        where shift_id = %s
                        and request_type = %s
                        and status = 'PENDING'
                        limit 1
                        """,
                        (
                            shift_id,
                            request_type,
                        ),
                    )

                    if cur.fetchone():
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "A pending correction request "
                                "already exists for this shift."
                            ),
                        )

                cur.execute(
                    """
                    insert into public.timekeeping_adjustment_requests (
                        employee_id,
                        department_id,
                        shift_id,
                        request_type,
                        requested_clock_in,
                        requested_clock_out,
                        reason,
                        status
                    )
                    values (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'PENDING'
                    )
                    returning id
                    """,
                    (
                        employee_id,
                        department["id"],
                        shift_id,
                        request_type,
                        requested_clock_in,
                        requested_clock_out,
                        request.reason.strip(),
                    ),
                )

                adjustment_id = (
                    cur.fetchone()[0]
                )

                conn.commit()

        message = (
            "⚠️ **TIMEKEEPING ADJUSTMENT REQUEST**\n"
            f"**Member:** {request.avatar_name}\n"
            f"**Department:** {department['name']}\n"
            f"**Request:** {request_type.replace('_', ' ')}\n"
        )

        if existing_clock_in:
            message += (
                "**Current Clock In:** "
                f"<t:{discord_timestamp(existing_clock_in)}:F>\n"
            )

        if existing_clock_out:
            message += (
                "**Current Clock Out:** "
                f"<t:{discord_timestamp(existing_clock_out)}:F>\n"
            )

        if requested_clock_in:
            message += (
                "**Requested Clock In:** "
                f"<t:{discord_timestamp(requested_clock_in)}:F>\n"
            )

        if requested_clock_out:
            message += (
                "**Requested Clock Out:** "
                f"<t:{discord_timestamp(requested_clock_out)}:F>\n"
            )

        message += (
            f"**Reason:** {request.reason.strip()}\n\n"
            "Status: **PENDING ADMIN REVIEW**"
        )

        send_department_discord(
            department["code"],
            message,
        )

        return {
            "success": True,

            "adjustment_id": str(
                adjustment_id
            ),

            "status": "PENDING",

            "request_type": request_type,

            "message": (
                "Your request has been submitted "
                "for administrator review."
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to submit adjustment request: "
                f"{exc}"
            ),
        )


# ============================================================
# ADMIN - LIST ADJUSTMENTS
# ============================================================

@app.get(
    "/api/timekeeping/admin/adjustments"
)
def admin_adjustments(
    department_code: str = "HHFD",
    status: str = "PENDING",
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_code(
                        cur,
                        department_code,
                    )
                )

                cur.execute(
                    """
                    select
                        a.id,
                        a.request_type,
                        a.requested_clock_in,
                        a.requested_clock_out,
                        a.reason,
                        a.status,
                        a.requested_at,
                        a.reviewed_at,
                        a.reviewed_by,
                        a.review_notes,
                        a.shift_id,
                        e.avatar_uuid,
                        e.avatar_name,
                        s.clock_in,
                        s.clock_out,
                        s.activities,
                        s.status
                    from public.timekeeping_adjustment_requests a

                    join public.timekeeping_employees e
                        on e.id = a.employee_id

                    left join public.timekeeping_shifts s
                        on s.id = a.shift_id

                    where a.department_id = %s
                    and (
                        upper(%s) = 'ALL'
                        or a.status = upper(%s)
                    )

                    order by
                        case
                            when a.status = 'PENDING'
                            then 0
                            else 1
                        end,
                        a.requested_at desc
                    """,
                    (
                        department["id"],
                        status,
                        status,
                    ),
                )

                rows = cur.fetchall()

                results = []

                for row in rows:
                    results.append(
                        {
                            "adjustment_id": str(
                                row[0]
                            ),

                            "request_type": row[1],

                            "requested_clock_in": (
                                row[2].isoformat()
                                if row[2]
                                else None
                            ),

                            "requested_clock_out": (
                                row[3].isoformat()
                                if row[3]
                                else None
                            ),

                            "reason": row[4],

                            "status": row[5],

                            "requested_at": (
                                row[6].isoformat()
                            ),

                            "reviewed_at": (
                                row[7].isoformat()
                                if row[7]
                                else None
                            ),

                            "reviewed_by": row[8],

                            "review_notes": row[9],

                            "shift_id": (
                                str(row[10])
                                if row[10]
                                else None
                            ),

                            "avatar_uuid": str(
                                row[11]
                            ),

                            "avatar_name": row[12],

                            "current_clock_in": (
                                row[13].isoformat()
                                if row[13]
                                else None
                            ),

                            "current_clock_out": (
                                row[14].isoformat()
                                if row[14]
                                else None
                            ),

                            "activities": row[15],

                            "shift_status": row[16],
                        }
                    )

                return {
                    "department": department,
                    "count": len(results),
                    "adjustments": results,
                }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to load adjustment requests: "
                f"{exc}"
            ),
        )


# ============================================================
# ADMIN - APPROVE ADJUSTMENT
# ============================================================

@app.post(
    "/api/timekeeping/admin/adjustments/{adjustment_id}/approve"
)
def approve_adjustment(
    adjustment_id: UUID,
    request: AdminAdjustmentReviewRequest,
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    select
                        a.employee_id,
                        a.department_id,
                        a.shift_id,
                        a.request_type,
                        a.requested_clock_in,
                        a.requested_clock_out,
                        a.reason,
                        a.status,
                        e.avatar_name,
                        d.code,
                        d.name
                    from public.timekeeping_adjustment_requests a

                    join public.timekeeping_employees e
                        on e.id = a.employee_id

                    join public.timekeeping_departments d
                        on d.id = a.department_id

                    where a.id = %s
                    limit 1
                    """,
                    (
                        str(adjustment_id),
                    ),
                )

                row = cur.fetchone()

                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            "Adjustment request not found."
                        ),
                    )

                if row[7] != "PENDING":
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This request has already "
                            "been reviewed."
                        ),
                    )

                employee_id = row[0]
                department_id = row[1]
                shift_id = row[2]

                request_type = row[3]

                requested_in = row[4]
                requested_out = row[5]

                reason = row[6]

                avatar_name = row[8]
                department_code = row[9]
                department_name = row[10]

                resulting_shift_id = (
                    shift_id
                )

                if (
                    request_type
                    == "MISSED_SHIFT"
                ):
                    cur.execute(
                        """
                        insert into public.timekeeping_shifts (
                            employee_id,
                            department_id,
                            clock_in,
                            clock_out,
                            activities,
                            status,
                            source
                        )
                        values (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            'CLOSED',
                            'ADJUSTMENT'
                        )
                        returning id
                        """,
                        (
                            employee_id,
                            department_id,
                            requested_in,
                            requested_out,
                            (
                                "Approved missed shift request. "
                                f"Reason: {reason}"
                            ),
                        ),
                    )

                    resulting_shift_id = (
                        cur.fetchone()[0]
                    )

                    cur.execute(
                        """
                        insert into public.timekeeping_shift_audit (
                            shift_id,
                            action,
                            changed_by,
                            reason,
                            new_clock_in,
                            new_clock_out,
                            new_activities
                        )
                        values (
                            %s,
                            'ADD_SHIFT',
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            resulting_shift_id,
                            request.reviewed_by,
                            reason,
                            requested_in,
                            requested_out,
                            (
                                "Approved missed shift request."
                            ),
                        ),
                    )

                else:
                    cur.execute(
                        """
                        select
                            clock_in,
                            clock_out,
                            activities,
                            status
                        from public.timekeeping_shifts
                        where id = %s
                        limit 1
                        """,
                        (
                            shift_id,
                        ),
                    )

                    shift = cur.fetchone()

                    if not shift:
                        raise HTTPException(
                            status_code=404,
                            detail=(
                                "The shift attached to this "
                                "request no longer exists."
                            ),
                        )

                    old_in = shift[0]
                    old_out = shift[1]
                    old_activities = shift[2]
                    old_status = shift[3]

                    new_in = old_in
                    new_out = old_out
                    new_status = old_status

                    if (
                        request_type
                        == "CLOCK_IN"
                    ):
                        new_in = requested_in

                    if (
                        request_type
                        == "CLOCK_OUT"
                    ):
                        new_out = requested_out

                        if old_status == "OPEN":
                            new_status = "CLOSED"

                    if (
                        new_out
                        and new_out <= new_in
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "The requested correction would "
                                "make clock-out earlier than clock-in."
                            ),
                        )

                    cur.execute(
                        """
                        update public.timekeeping_shifts
                        set
                            clock_in = %s,
                            clock_out = %s,
                            status = %s
                        where id = %s
                        """,
                        (
                            new_in,
                            new_out,
                            new_status,
                            shift_id,
                        ),
                    )

                    cur.execute(
                        """
                        insert into public.timekeeping_shift_audit (
                            shift_id,
                            action,
                            changed_by,
                            reason,
                            previous_clock_in,
                            previous_clock_out,
                            new_clock_in,
                            new_clock_out,
                            previous_activities,
                            new_activities
                        )
                        values (
                            %s,
                            'EDIT_SHIFT',
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            shift_id,
                            request.reviewed_by,
                            reason,
                            old_in,
                            old_out,
                            new_in,
                            new_out,
                            old_activities,
                            old_activities,
                        ),
                    )

                cur.execute(
                    """
                    update public.timekeeping_adjustment_requests
                    set
                        status = 'APPROVED',
                        reviewed_at = now(),
                        reviewed_by = %s,
                        review_notes = %s,
                        shift_id = %s
                    where id = %s
                    """,
                    (
                        request.reviewed_by,
                        request.review_notes,
                        resulting_shift_id,
                        str(adjustment_id),
                    ),
                )

                conn.commit()

        send_department_discord(
            department_code,
            (
                "✅ **TIME CORRECTION APPROVED**\n"
                f"**Member:** {avatar_name}\n"
                f"**Department:** {department_name}\n"
                f"**Type:** "
                f"{request_type.replace('_', ' ')}\n"
                f"**Approved By:** "
                f"{request.reviewed_by}"
            ),
        )

        return {
            "success": True,
            "status": "APPROVED",

            "adjustment_id": str(
                adjustment_id
            ),

            "shift_id": str(
                resulting_shift_id
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to approve adjustment: "
                f"{exc}"
            ),
        )


# ============================================================
# ADMIN - DENY ADJUSTMENT
# ============================================================

@app.post(
    "/api/timekeeping/admin/adjustments/{adjustment_id}/deny"
)
def deny_adjustment(
    adjustment_id: UUID,
    request: AdminAdjustmentReviewRequest,
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    select
                        a.status,
                        a.request_type,
                        e.avatar_name,
                        d.code,
                        d.name
                    from public.timekeeping_adjustment_requests a

                    join public.timekeeping_employees e
                        on e.id = a.employee_id

                    join public.timekeeping_departments d
                        on d.id = a.department_id

                    where a.id = %s
                    limit 1
                    """,
                    (
                        str(adjustment_id),
                    ),
                )

                row = cur.fetchone()

                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            "Adjustment request not found."
                        ),
                    )

                if row[0] != "PENDING":
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This request has already "
                            "been reviewed."
                        ),
                    )

                cur.execute(
                    """
                    update public.timekeeping_adjustment_requests
                    set
                        status = 'DENIED',
                        reviewed_at = now(),
                        reviewed_by = %s,
                        review_notes = %s
                    where id = %s
                    """,
                    (
                        request.reviewed_by,
                        request.review_notes,
                        str(adjustment_id),
                    ),
                )

                conn.commit()

        send_department_discord(
            row[3],
            (
                "❌ **TIME CORRECTION DENIED**\n"
                f"**Member:** {row[2]}\n"
                f"**Department:** {row[4]}\n"
                f"**Type:** "
                f"{row[1].replace('_', ' ')}\n"
                f"**Reviewed By:** "
                f"{request.reviewed_by}"
            ),
        )

        return {
            "success": True,
            "status": "DENIED",

            "adjustment_id": str(
                adjustment_id
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to deny adjustment: "
                f"{exc}"
            ),
        )


# ============================================================
# ADMIN - LIST SHIFTS
# ============================================================

@app.get(
    "/api/timekeeping/admin/shifts"
)
def admin_list_shifts(
    department_code: str = "HHFD",
    limit: int = 100,
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    limit = max(
        1,
        min(limit, 500),
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_code(
                        cur,
                        department_code,
                    )
                )

                cur.execute(
                    """
                    select
                        s.id,
                        e.avatar_uuid,
                        e.avatar_name,
                        s.clock_in,
                        s.clock_out,
                        s.activities,
                        s.status,
                        s.source
                    from public.timekeeping_shifts s

                    join public.timekeeping_employees e
                        on e.id = s.employee_id

                    where s.department_id = %s

                    order by s.clock_in desc

                    limit %s
                    """,
                    (
                        department["id"],
                        limit,
                    ),
                )

                rows = cur.fetchall()

                shifts = []

                for row in rows:
                    duration = None

                    if (
                        row[3]
                        and row[4]
                    ):
                        duration = (
                            format_duration(
                                int(
                                    (
                                        row[4]
                                        - row[3]
                                    ).total_seconds()
                                )
                            )
                        )

                    shifts.append(
                        {
                            "shift_id": str(
                                row[0]
                            ),

                            "avatar_uuid": str(
                                row[1]
                            ),

                            "avatar_name": row[2],

                            "clock_in": (
                                row[3].isoformat()
                            ),

                            "clock_out": (
                                row[4].isoformat()
                                if row[4]
                                else None
                            ),

                            "duration": duration,

                            "activities": row[5],

                            "status": row[6],

                            "source": row[7],
                        }
                    )

                return {
                    "department": department,
                    "count": len(shifts),
                    "shifts": shifts,
                }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to load shifts: {exc}"
            ),
        )


# ============================================================
# ADMIN - OPEN SHIFTS
# ============================================================

@app.get(
    "/api/timekeeping/admin/open-shifts"
)
def admin_open_shifts(
    department_code: str = "HHFD",
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_code(
                        cur,
                        department_code,
                    )
                )

                cur.execute(
                    """
                    select
                        s.id,
                        e.avatar_uuid,
                        e.avatar_name,
                        s.clock_in
                    from public.timekeeping_shifts s

                    join public.timekeeping_employees e
                        on e.id = s.employee_id

                    where s.department_id = %s
                    and s.status = 'OPEN'

                    order by s.clock_in asc
                    """,
                    (
                        department["id"],
                    ),
                )

                rows = cur.fetchall()

                now = datetime.now(
                    timezone.utc
                )

                open_shifts = []

                for row in rows:
                    seconds = int(
                        (
                            now
                            - row[3]
                        ).total_seconds()
                    )

                    open_shifts.append(
                        {
                            "shift_id": str(
                                row[0]
                            ),

                            "avatar_uuid": str(
                                row[1]
                            ),

                            "avatar_name": row[2],

                            "clock_in": (
                                row[3].isoformat()
                            ),

                            "duration": (
                                format_duration(
                                    seconds
                                )
                            ),
                        }
                    )

                return {
                    "department": department,
                    "count": len(
                        open_shifts
                    ),
                    "open_shifts": open_shifts,
                }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to load open shifts: {exc}"
            ),
        )


# ============================================================
# ADMIN - WEEKLY HOURS
# ============================================================

@app.get(
    "/api/timekeeping/admin/weekly-hours"
)
def admin_weekly_hours(
    department_code: str = "HHFD",
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    try:
        local_timezone = ZoneInfo(
            SYSTEM_TIMEZONE
        )

        now_local = datetime.now(
            local_timezone
        )

        start_local = (
            now_local
            - timedelta(
                days=now_local.weekday()
            )
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        end_local = (
            start_local
            + timedelta(
                days=7
            )
        )

        start_utc = (
            start_local.astimezone(
                timezone.utc
            )
        )

        end_utc = (
            end_local.astimezone(
                timezone.utc
            )
        )

        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_code(
                        cur,
                        department_code,
                    )
                )

                cur.execute(
                    """
                    select
                        e.avatar_uuid,
                        e.avatar_name,
                        coalesce(
                            sum(
                                extract(
                                    epoch from (
                                        s.clock_out
                                        - s.clock_in
                                    )
                                )
                            ),
                            0
                        ) as total_seconds

                    from public.timekeeping_shifts s

                    join public.timekeeping_employees e
                        on e.id = s.employee_id

                    where s.department_id = %s
                    and s.status = 'CLOSED'
                    and s.clock_in >= %s
                    and s.clock_in < %s

                    group by
                        e.avatar_uuid,
                        e.avatar_name

                    order by total_seconds desc
                    """,
                    (
                        department["id"],
                        start_utc,
                        end_utc,
                    ),
                )

                rows = cur.fetchall()

                employees = []

                department_total = 0

                for row in rows:
                    seconds = int(
                        row[2] or 0
                    )

                    department_total += seconds

                    employees.append(
                        {
                            "avatar_uuid": str(
                                row[0]
                            ),

                            "avatar_name": row[1],

                            "duration": (
                                format_duration(
                                    seconds
                                )
                            ),
                        }
                    )

                return {
                    "department": department,

                    "timezone": (
                        SYSTEM_TIMEZONE
                    ),

                    "week_start": (
                        start_local.isoformat()
                    ),

                    "week_end": (
                        end_local.isoformat()
                    ),

                    "employees": employees,

                    "department_total": (
                        format_duration(
                            department_total
                        )
                    ),
                }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to calculate weekly hours: "
                f"{exc}"
            ),
        )


# ============================================================
# ADMIN - EDIT SHIFT
# ============================================================

@app.patch(
    "/api/timekeeping/admin/shifts/{shift_id}"
)
def admin_edit_shift(
    shift_id: UUID,
    request: AdminShiftEditRequest,
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    select
                        clock_in,
                        clock_out,
                        activities,
                        status
                    from public.timekeeping_shifts
                    where id = %s
                    limit 1
                    """,
                    (
                        str(shift_id),
                    ),
                )

                shift = cur.fetchone()

                if not shift:
                    raise HTTPException(
                        status_code=404,
                        detail="Shift not found.",
                    )

                old_in = shift[0]
                old_out = shift[1]

                old_activities = shift[2]
                old_status = shift[3]

                new_in = (
                    ensure_timezone(
                        request.clock_in
                    )
                    if request.clock_in
                    else old_in
                )

                new_out = (
                    ensure_timezone(
                        request.clock_out
                    )
                    if request.clock_out
                    else old_out
                )

                new_activities = (
                    request.activities
                    if request.activities
                    is not None
                    else old_activities
                )

                new_status = old_status

                if (
                    new_out is not None
                    and old_status == "OPEN"
                ):
                    new_status = "CLOSED"

                if (
                    new_out
                    and new_out <= new_in
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Clock-out must be after clock-in."
                        ),
                    )

                cur.execute(
                    """
                    update public.timekeeping_shifts
                    set
                        clock_in = %s,
                        clock_out = %s,
                        activities = %s,
                        status = %s
                    where id = %s
                    """,
                    (
                        new_in,
                        new_out,
                        new_activities,
                        new_status,
                        str(shift_id),
                    ),
                )

                cur.execute(
                    """
                    insert into public.timekeeping_shift_audit (
                        shift_id,
                        action,
                        changed_by,
                        reason,
                        previous_clock_in,
                        previous_clock_out,
                        new_clock_in,
                        new_clock_out,
                        previous_activities,
                        new_activities
                    )
                    values (
                        %s,
                        'EDIT_SHIFT',
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        str(shift_id),
                        request.changed_by,
                        request.reason,
                        old_in,
                        old_out,
                        new_in,
                        new_out,
                        old_activities,
                        new_activities,
                    ),
                )

                conn.commit()

        return {
            "success": True,

            "shift_id": str(
                shift_id
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to edit shift: {exc}"
            ),
        )


# ============================================================
# ADMIN - ADD SHIFT
# ============================================================

@app.post(
    "/api/timekeeping/admin/shifts"
)
def admin_add_shift(
    request: AdminShiftAddRequest,
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    clock_in = ensure_timezone(
        request.clock_in
    )

    clock_out = ensure_timezone(
        request.clock_out
    )

    if clock_out <= clock_in:
        raise HTTPException(
            status_code=400,
            detail=(
                "Clock-out must be after clock-in."
            ),
        )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = (
                    get_department_by_code(
                        cur,
                        request.department_code,
                    )
                )

                employee_id = (
                    get_or_create_employee(
                        cur,
                        request.avatar_uuid,
                        request.avatar_name,
                    )
                )

                ensure_membership(
                    cur,
                    employee_id,
                    department["id"],
                )

                cur.execute(
                    """
                    insert into public.timekeeping_shifts (
                        employee_id,
                        department_id,
                        clock_in,
                        clock_out,
                        activities,
                        status,
                        source
                    )
                    values (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'CLOSED',
                        'ADMIN'
                    )
                    returning id
                    """,
                    (
                        employee_id,
                        department["id"],
                        clock_in,
                        clock_out,
                        request.activities,
                    ),
                )

                shift_id = (
                    cur.fetchone()[0]
                )

                cur.execute(
                    """
                    insert into public.timekeeping_shift_audit (
                        shift_id,
                        action,
                        changed_by,
                        reason,
                        new_clock_in,
                        new_clock_out,
                        new_activities
                    )
                    values (
                        %s,
                        'ADD_SHIFT',
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        shift_id,
                        request.changed_by,
                        request.reason,
                        clock_in,
                        clock_out,
                        request.activities,
                    ),
                )

                conn.commit()

        return {
            "success": True,

            "shift_id": str(
                shift_id
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to add shift: {exc}"
            ),
        )


# ============================================================
# ADMIN - VOID SHIFT
# ============================================================

@app.post(
    "/api/timekeeping/admin/shifts/{shift_id}/void"
)
def admin_void_shift(
    shift_id: UUID,
    request: AdminShiftVoidRequest,
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    select
                        clock_in,
                        clock_out,
                        activities,
                        status
                    from public.timekeeping_shifts
                    where id = %s
                    limit 1
                    """,
                    (
                        str(shift_id),
                    ),
                )

                shift = cur.fetchone()

                if not shift:
                    raise HTTPException(
                        status_code=404,
                        detail="Shift not found.",
                    )

                if shift[3] == "VOID":
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Shift is already void."
                        ),
                    )

                cur.execute(
                    """
                    update public.timekeeping_shifts
                    set status = 'VOID'
                    where id = %s
                    """,
                    (
                        str(shift_id),
                    ),
                )

                cur.execute(
                    """
                    insert into public.timekeeping_shift_audit (
                        shift_id,
                        action,
                        changed_by,
                        reason,
                        previous_clock_in,
                        previous_clock_out,
                        previous_activities
                    )
                    values (
                        %s,
                        'VOID_SHIFT',
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        str(shift_id),
                        request.changed_by,
                        request.reason,
                        shift[0],
                        shift[1],
                        shift[2],
                    ),
                )

                conn.commit()

        return {
            "success": True,

            "shift_id": str(
                shift_id
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to void shift: {exc}"
            ),
        )


# ============================================================
# ADMIN - AUDIT HISTORY
# ============================================================

@app.get(
    "/api/timekeeping/admin/shifts/{shift_id}/audit"
)
def admin_shift_audit(
    shift_id: UUID,
    x_admin_key: Optional[str] = Header(
        default=None
    ),
):
    verify_admin_api_key(
        x_admin_key
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    select
                        id,
                        action,
                        changed_by,
                        reason,
                        previous_clock_in,
                        previous_clock_out,
                        new_clock_in,
                        new_clock_out,
                        previous_activities,
                        new_activities,
                        created_at
                    from public.timekeeping_shift_audit
                    where shift_id = %s
                    order by created_at asc
                    """,
                    (
                        str(shift_id),
                    ),
                )

                rows = cur.fetchall()

                audit = []

                for row in rows:
                    audit.append(
                        {
                            "audit_id": str(
                                row[0]
                            ),

                            "action": row[1],

                            "changed_by": row[2],

                            "reason": row[3],

                            "previous_clock_in": (
                                row[4].isoformat()
                                if row[4]
                                else None
                            ),

                            "previous_clock_out": (
                                row[5].isoformat()
                                if row[5]
                                else None
                            ),

                            "new_clock_in": (
                                row[6].isoformat()
                                if row[6]
                                else None
                            ),

                            "new_clock_out": (
                                row[7].isoformat()
                                if row[7]
                                else None
                            ),

                            "previous_activities": (
                                row[8]
                            ),

                            "new_activities": (
                                row[9]
                            ),

                            "created_at": (
                                row[10].isoformat()
                            ),
                        }
                    )

                return {
                    "shift_id": str(
                        shift_id
                    ),

                    "audit_count": len(
                        audit
                    ),

                    "audit": audit,
                }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to load audit history: "
                f"{exc}"
            ),
        )


# ============================================================
# ADMIN WEB PAGE
# ============================================================

@app.get(
    "/admin",
    include_in_schema=False,
)
def admin_console():
    return FileResponse(
        "static/admin.html"
    )
