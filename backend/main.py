import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


# ============================================================
# HARMONY HILLS TIMEKEEPING
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")
CLOCK_API_KEY = os.getenv("CLOCK_API_KEY")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")

SYSTEM_TIMEZONE = os.getenv(
    "SYSTEM_TIMEZONE",
    "America/New_York",
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
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Harmony Hills Timekeeping",
    description="Harmony Hills personnel timekeeping system.",
    version="0.3.0",
)


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
# AUTHENTICATION
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

    local_timezone = ZoneInfo(
        SYSTEM_TIMEZONE
    )

    return value.replace(
        tzinfo=local_timezone
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
        clock_id = row[0]
        registered_department = row[1]
        active = row[2]

        if not active:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This Harmony Hills Time Clock "
                    "has been disabled."
                ),
            )

        if registered_department != department_id:
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
                clock_id,
            ),
        )

        return clock_id

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
# EMPLOYEE LOOKUP / CREATION
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
        employee_id = row[0]
        active = row[1]

        if not active:
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
                employee_id,
            ),
        )

        return employee_id

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
            "version": "0.3.0",
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

                employee_id = employee[0]

                cur.execute(
                    """
                    select
                        id,
                        clock_in
                    from public.timekeeping_shifts
                    where employee_id = %s
                    and department_id = %s
                    and status = 'OPEN'
                    order by clock_in desc
                    limit 1
                    """,
                    (
                        employee_id,
                        department["id"],
                    ),
                )

                shift = cur.fetchone()

                if not shift:
                    return {
                        "success": True,
                        "clocked_in": False,
                        "department": {
                            "code": department["code"],
                            "name": department["name"],
                        },
                    }

                now = datetime.now(
                    timezone.utc
                )

                seconds = int(
                    (
                        now
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

                cur.execute(
                    """
                    select
                        id,
                        clock_in
                    from public.timekeeping_shifts
                    where employee_id = %s
                    and department_id = %s
                    and status = 'OPEN'
                    order by clock_in desc
                    limit 1
                    """,
                    (
                        employee_id,
                        department["id"],
                    ),
                )

                existing = cur.fetchone()

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

                shift_id = cur.fetchone()[0]

                conn.commit()

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
                    "employee": {
                        "avatar_uuid": str(
                            request.avatar_uuid
                        ),
                        "avatar_name": (
                            request.avatar_name
                        ),
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

                cur.execute(
                    """
                    select
                        id,
                        clock_in
                    from public.timekeeping_shifts
                    where employee_id = %s
                    and department_id = %s
                    and status = 'OPEN'
                    order by clock_in desc
                    limit 1
                    """,
                    (
                        employee_id,
                        department["id"],
                    ),
                )

                shift = cur.fetchone()

                if not shift:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "No open shift was found "
                            "for this employee."
                        ),
                    )

                shift_id = shift[0]
                clock_in_time = shift[1]

                clock_out_time = datetime.now(
                    timezone.utc
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
                        shift_id,
                    ),
                )

                conn.commit()

                total_seconds = int(
                    (
                        clock_out_time
                        - clock_in_time
                    ).total_seconds()
                )

                return {
                    "success": True,
                    "action": "CLOCK_OUT",
                    "shift_id": str(
                        shift_id
                    ),
                    "department": {
                        "code": department["code"],
                        "name": department["name"],
                    },
                    "employee": {
                        "avatar_uuid": str(
                            request.avatar_uuid
                        ),
                        "avatar_name": (
                            request.avatar_name
                        ),
                    },
                    "clock_in": (
                        clock_in_time.isoformat()
                    ),
                    "clock_out": (
                        clock_out_time.isoformat()
                    ),
                    "duration": (
                        format_duration(
                            total_seconds
                        )
                    ),
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

                    if row[3] and row[4]:
                        seconds = int(
                            (
                                row[4]
                                - row[3]
                            ).total_seconds()
                        )

                        duration = (
                            format_duration(
                                seconds
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
                            "avatar_name": (
                                row[2]
                            ),
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
                    "count": len(
                        shifts
                    ),
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

                shifts = []

                for row in rows:
                    seconds = int(
                        (
                            now
                            - row[3]
                        ).total_seconds()
                    )

                    shifts.append(
                        {
                            "shift_id": str(
                                row[0]
                            ),
                            "avatar_uuid": str(
                                row[1]
                            ),
                            "avatar_name": (
                                row[2]
                            ),
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
                        shifts
                    ),
                    "open_shifts": shifts,
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

                    department_total += (
                        seconds
                    )

                    employees.append(
                        {
                            "avatar_uuid": str(
                                row[0]
                            ),
                            "avatar_name": (
                                row[1]
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

                old_clock_in = shift[0]
                old_clock_out = shift[1]
                old_activities = shift[2]

                new_clock_in = (
                    ensure_timezone(
                        request.clock_in
                    )
                    if request.clock_in is not None
                    else old_clock_in
                )

                new_clock_out = (
                    ensure_timezone(
                        request.clock_out
                    )
                    if request.clock_out is not None
                    else old_clock_out
                )

                new_activities = (
                    request.activities
                    if request.activities is not None
                    else old_activities
                )

                if (
                    new_clock_out
                    and new_clock_out < new_clock_in
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Clock-out cannot be "
                            "before clock-in."
                        ),
                    )

                cur.execute(
                    """
                    update public.timekeeping_shifts
                    set
                        clock_in = %s,
                        clock_out = %s,
                        activities = %s
                    where id = %s
                    """,
                    (
                        new_clock_in,
                        new_clock_out,
                        new_activities,
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
                        old_clock_in,
                        old_clock_out,
                        new_clock_in,
                        new_clock_out,
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
                    "message": (
                        "Shift updated successfully."
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
# ADMIN - ADD MISSED SHIFT
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

                seconds = int(
                    (
                        clock_out
                        - clock_in
                    ).total_seconds()
                )

                return {
                    "success": True,
                    "shift_id": str(
                        shift_id
                    ),
                    "department": department,
                    "duration": (
                        format_duration(
                            seconds
                        )
                    ),
                    "message": (
                        "Missed shift added successfully."
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
                    "message": (
                        "Shift voided successfully."
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
                    select id
                    from public.timekeeping_shifts
                    where id = %s
                    limit 1
                    """,
                    (
                        str(shift_id),
                    ),
                )

                if not cur.fetchone():
                    raise HTTPException(
                        status_code=404,
                        detail="Shift not found.",
                    )

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

                audit_entries = []

                for row in rows:
                    audit_entries.append(
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
                        audit_entries
                    ),
                    "audit": audit_entries,
                }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to load audit history: "
                f"{exc}"
            ),
        )


# ============================================================
# ADMIN WEB CONSOLE
# ============================================================

@app.get(
    "/admin",
    include_in_schema=False,
)
def admin_console():
    return FileResponse(
        "static/admin.html"
    )
