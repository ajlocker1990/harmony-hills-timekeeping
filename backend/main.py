import os
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")
CLOCK_API_KEY = os.getenv("CLOCK_API_KEY")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not configured.")

if not CLOCK_API_KEY:
    raise RuntimeError("CLOCK_API_KEY environment variable is not configured.")


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Harmony Hills Timekeeping",
    version="0.1.0",
)


# ============================================================
# REQUEST MODELS
# ============================================================

class ClockInRequest(BaseModel):
    avatar_uuid: UUID
    avatar_name: str = Field(min_length=1, max_length=200)

    # Group assigned to the in-world time clock.
    group_uuid: UUID

    # UUID of the actual SL clock object.
    clock_uuid: UUID

    clock_name: str | None = Field(
        default=None,
        max_length=200,
    )


class ClockOutRequest(BaseModel):
    avatar_uuid: UUID
    avatar_name: str = Field(min_length=1, max_length=200)

    group_uuid: UUID
    clock_uuid: UUID

    clock_name: str | None = Field(
        default=None,
        max_length=200,
    )

    activities: str = Field(
        min_length=1,
        max_length=1500,
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

def verify_clock_api_key(api_key: str | None):
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key.",
        )

    if api_key != CLOCK_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )


# ============================================================
# DEPARTMENT LOOKUP
# ============================================================

def get_department_by_group(cur, group_uuid: UUID):
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
        (str(group_uuid),),
    )

    department = cur.fetchone()

    if not department:
        raise HTTPException(
            status_code=403,
            detail="This Second Life group is not registered to a Harmony Hills department.",
        )

    if not department[3]:
        raise HTTPException(
            status_code=403,
            detail="This department is currently inactive.",
        )

    return {
        "id": department[0],
        "code": department[1],
        "name": department[2],
    }


# ============================================================
# CLOCK REGISTRATION / VALIDATION
# ============================================================

def validate_or_register_clock(
    cur,
    clock_uuid: UUID,
    clock_name: str | None,
    department_id,
):
    """
    First contact:
        Registers the in-world clock.

    Later contacts:
        Ensures that clock still belongs to the same department.
    """

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
        (str(clock_uuid),),
    )

    existing = cur.fetchone()

    if existing:
        if not existing[2]:
            raise HTTPException(
                status_code=403,
                detail="This time clock has been disabled.",
            )

        if existing[1] != department_id:
            raise HTTPException(
                status_code=403,
                detail="This time clock is registered to another department.",
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
                existing[0],
            ),
        )

        return existing[0]

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
# EMPLOYEE
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
        (str(avatar_uuid),),
    )

    employee = cur.fetchone()

    if employee:
        if not employee[1]:
            raise HTTPException(
                status_code=403,
                detail="This employee has been disabled.",
            )

        cur.execute(
            """
            update public.timekeeping_employees
            set avatar_name = %s
            where id = %s
            """,
            (
                avatar_name,
                employee[0],
            ),
        )

        return employee[0]

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

    membership = cur.fetchone()

    if membership:
        if not membership[1]:
            raise HTTPException(
                status_code=403,
                detail="Your membership in this department is inactive.",
            )

        return membership[0]

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
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("select 1;")
                cur.fetchone()

        return {
            "status": "ok",
            "service": "Harmony Hills Timekeeping",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database connection failed: {exc}",
        )


# ============================================================
# CLOCK IN
# ============================================================

@app.post("/api/timekeeping/clock-in")
def clock_in(
    request: ClockInRequest,
    x_api_key: str | None = Header(default=None),
):
    verify_clock_api_key(x_api_key)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                # Determine department from SL group.
                department = get_department_by_group(
                    cur,
                    request.group_uuid,
                )

                # Register / validate clock.
                validate_or_register_clock(
                    cur,
                    request.clock_uuid,
                    request.clock_name,
                    department["id"],
                )

                # Find/create employee.
                employee_id = get_or_create_employee(
                    cur,
                    request.avatar_uuid,
                    request.avatar_name,
                )

                # Ensure departmental membership.
                ensure_membership(
                    cur,
                    employee_id,
                    department["id"],
                )

                # Check for existing open shift.
                cur.execute(
                    """
                    select
                        id,
                        clock_in
                    from public.timekeeping_shifts
                    where employee_id = %s
                    and department_id = %s
                    and status = 'OPEN'
                    limit 1
                    """,
                    (
                        employee_id,
                        department["id"],
                    ),
                )

                existing_shift = cur.fetchone()

                if existing_shift:
                    conn.rollback()

                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "You are already clocked in.",
                            "shift_id": str(existing_shift[0]),
                            "clock_in": existing_shift[1].isoformat(),
                        },
                    )

                # Server time is authoritative.
                clock_in_time = datetime.now(timezone.utc)

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
                    "shift_id": str(shift_id),
                    "department": {
                        "code": department["code"],
                        "name": department["name"],
                    },
                    "employee": {
                        "avatar_uuid": str(request.avatar_uuid),
                        "avatar_name": request.avatar_name,
                    },
                    "clock_in": clock_in_time.isoformat(),
                }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Clock-in failed: {exc}",
        )


# ============================================================
# CLOCK OUT
# ============================================================

@app.post("/api/timekeeping/clock-out")
def clock_out(
    request: ClockOutRequest,
    x_api_key: str | None = Header(default=None),
):
    verify_clock_api_key(x_api_key)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                department = get_department_by_group(
                    cur,
                    request.group_uuid,
                )

                validate_or_register_clock(
                    cur,
                    request.clock_uuid,
                    request.clock_name,
                    department["id"],
                )

                employee_id = get_or_create_employee(
                    cur,
                    request.avatar_uuid,
                    request.avatar_name,
                )

                ensure_membership(
                    cur,
                    employee_id,
                    department["id"],
                )

                # Find open shift.
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
                    conn.rollback()

                    raise HTTPException(
                        status_code=409,
                        detail="No open shift was found for this employee in this department.",
                    )

                shift_id = shift[0]
                clock_in_time = shift[1]

                clock_out_time = datetime.now(timezone.utc)

                activities = request.activities.strip()

                if not activities:
                    conn.rollback()

                    raise HTTPException(
                        status_code=400,
                        detail="Shift activities are required.",
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

                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60

                return {
                    "success": True,
                    "action": "CLOCK_OUT",
                    "shift_id": str(shift_id),
                    "department": {
                        "code": department["code"],
                        "name": department["name"],
                    },
                    "employee": {
                        "avatar_uuid": str(request.avatar_uuid),
                        "avatar_name": request.avatar_name,
                    },
                    "clock_in": clock_in_time.isoformat(),
                    "clock_out": clock_out_time.isoformat(),
                    "duration": {
                        "seconds": total_seconds,
                        "hours": hours,
                        "minutes": minutes,
                    },
                    "activities": activities,
                }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Clock-out failed: {exc}",
        )
