import json
import os
from datetime import timezone
from typing import Optional

import httpx
import psycopg
from fastapi import APIRouter, Header, HTTPException, Request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


# ============================================================
# ROUTER
# ============================================================

router = APIRouter()


# ============================================================
# ENVIRONMENT
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")

DISCORD_APP_ID = os.getenv("DISCORD_APP_ID")
DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

DISCORD_ADMIN_USER_ID = os.getenv(
    "DISCORD_ADMIN_USER_ID"
)

DISCORD_ADMIN_GUILD_ID = os.getenv(
    "DISCORD_ADMIN_GUILD_ID"
)


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is not configured."
        )

    return psycopg.connect(
        DATABASE_URL,
        autocommit=False,
    )


# ============================================================
# DISCORD SIGNATURE VERIFICATION
# ============================================================

def verify_discord_signature(
    raw_body: bytes,
    signature: Optional[str],
    timestamp: Optional[str],
):
    if not DISCORD_PUBLIC_KEY:
        raise HTTPException(
            status_code=500,
            detail="Discord public key is not configured.",
        )

    if not signature or not timestamp:
        raise HTTPException(
            status_code=401,
            detail="Missing Discord signature.",
        )

    try:
        verify_key = VerifyKey(
            bytes.fromhex(
                DISCORD_PUBLIC_KEY
            )
        )

        verify_key.verify(
            timestamp.encode() + raw_body,
            bytes.fromhex(
                signature
            ),
        )

    except (
        BadSignatureError,
        ValueError,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Discord signature.",
        )


# ============================================================
# DISCORD TIME FORMAT
# ============================================================

def format_dt(
    value,
):
    if not value:
        return "—"

    unix_time = int(
        value
        .astimezone(
            timezone.utc
        )
        .timestamp()
    )

    return f"<t:{unix_time}:f>"


# ============================================================
# LOAD PENDING CORRECTIONS
# ============================================================

def load_pending_corrections():
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                select
                    a.id,
                    a.request_type,
                    a.requested_clock_in,
                    a.requested_clock_out,
                    a.reason,
                    a.requested_at,

                    e.avatar_name,

                    s.clock_in,
                    s.clock_out

                from public.timekeeping_adjustment_requests a

                join public.timekeeping_employees e
                    on e.id = a.employee_id

                join public.timekeeping_departments d
                    on d.id = a.department_id

                left join public.timekeeping_shifts s
                    on s.id = a.shift_id

                where d.code = 'HHFD'
                and a.status = 'PENDING'

                order by a.requested_at asc

                limit 10
                """
            )

            return cur.fetchall()


# ============================================================
# BUILD /CORRECTIONS MESSAGE
# ============================================================

def build_corrections_message(
    rows,
):
    if not rows:
        return (
            "✅ **HHFD Timekeeping**\n\n"
            "There are no pending time correction requests."
        )

    lines = [
        "⚠️ **HHFD Pending Time Corrections**",
        "",
        f"**Pending:** {len(rows)}",
        "",
    ]

    for index, row in enumerate(
        rows,
        start=1,
    ):
        (
            adjustment_id,
            request_type,
            requested_in,
            requested_out,
            reason,
            requested_at,
            avatar_name,
            current_in,
            current_out,
        ) = row

        request_label = (
            request_type
            .replace(
                "_",
                " "
            )
            .title()
        )

        lines.append(
            f"**{index}. {avatar_name}**"
        )

        lines.append(
            f"Type: **{request_label}**"
        )

        if request_type == "CLOCK_IN":
            lines.append(
                "Current: "
                + format_dt(
                    current_in
                )
            )

            lines.append(
                "Requested: "
                + format_dt(
                    requested_in
                )
            )

        elif request_type == "CLOCK_OUT":
            lines.append(
                "Current: "
                + format_dt(
                    current_out
                )
            )

            lines.append(
                "Requested: "
                + format_dt(
                    requested_out
                )
            )

        elif request_type == "MISSED_SHIFT":
            lines.append(
                "Requested In: "
                + format_dt(
                    requested_in
                )
            )

            lines.append(
                "Requested Out: "
                + format_dt(
                    requested_out
                )
            )

        lines.append(
            f"Reason: {reason or 'No reason provided.'}"
        )

        lines.append(
            "Submitted: "
            + format_dt(
                requested_at
            )
        )

        lines.append(
            f"Request ID: `{adjustment_id}`"
        )

        lines.append("")

    lines.append(
        "Approve or deny requests in the "
        "Harmony Hills Timekeeping Admin Console."
    )

    message = "\n".join(
        lines
    )

    if len(message) > 1900:
        message = (
            message[:1850]
            + "\n\n…Additional requests are available "
            + "in the Admin Console."
        )

    return message


# ============================================================
# REGISTER GUILD COMMAND
# ============================================================

async def register_commands():
    missing = []

    if not DISCORD_APP_ID:
        missing.append(
            "DISCORD_APP_ID"
        )

    if not DISCORD_BOT_TOKEN:
        missing.append(
            "DISCORD_BOT_TOKEN"
        )

    if not DISCORD_ADMIN_GUILD_ID:
        missing.append(
            "DISCORD_ADMIN_GUILD_ID"
        )

    if missing:
        print(
            "Discord command registration skipped. "
            "Missing:",
            ", ".join(
                missing
            ),
        )

        return

    url = (
        "https://discord.com/api/v10/"
        f"applications/{DISCORD_APP_ID}/"
        f"guilds/{DISCORD_ADMIN_GUILD_ID}/commands"
    )

    payload = {
        "name": "corrections",

        "description": (
            "View pending HHFD time correction requests"
        ),

        "type": 1,
    }

    headers = {
        "Authorization":
            f"Bot {DISCORD_BOT_TOKEN}",

        "Content-Type":
            "application/json",
    }

    try:
        async with httpx.AsyncClient(
            timeout=15.0
        ) as client:

            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )

        print(
            "Discord guild command registration status:",
            response.status_code,
        )

        print(
            "Discord guild command registration response:",
            response.text,
        )

        response.raise_for_status()

        print(
            "Discord guild /corrections command registered."
        )

    except Exception as exc:
        print(
            "Discord guild command registration failed:",
            repr(
                exc
            ),
        )


# ============================================================
# INTERACTION ENDPOINT
# ============================================================

@router.post(
    "/discord/interactions",
    include_in_schema=False,
)
async def discord_interactions(
    request: Request,

    x_signature_ed25519: Optional[str] = Header(
        default=None
    ),

    x_signature_timestamp: Optional[str] = Header(
        default=None
    ),
):
    raw_body = await request.body()

    verify_discord_signature(
        raw_body,
        x_signature_ed25519,
        x_signature_timestamp,
    )

    try:
        payload = json.loads(
            raw_body
        )

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid Discord payload.",
        )

    interaction_type = payload.get(
        "type"
    )


    # ========================================================
    # DISCORD PING
    # ========================================================

    if interaction_type == 1:
        return {
            "type": 1
        }


    # ========================================================
    # APPLICATION COMMAND ONLY
    # ========================================================

    if interaction_type != 2:
        return {
            "type": 4,

            "data": {
                "content":
                    "Unsupported interaction.",

                "flags": 64,
            },
        }


    # ========================================================
    # VERIFY SERVER
    # ========================================================

    guild_id = payload.get(
        "guild_id"
    )

    if (
        DISCORD_ADMIN_GUILD_ID
        and guild_id
        != DISCORD_ADMIN_GUILD_ID
    ):
        return {
            "type": 4,

            "data": {
                "content":
                    "⛔ This command is only available "
                    "in the Timekeeping Admin server.",

                "flags": 64,
            },
        }


    # ========================================================
    # IDENTIFY USER
    # ========================================================

    discord_user_id = None

    if payload.get("member"):
        discord_user_id = (
            payload["member"]
            .get(
                "user",
                {}
            )
            .get(
                "id"
            )
        )

    elif payload.get("user"):
        discord_user_id = (
            payload["user"]
            .get(
                "id"
            )
        )


    # ========================================================
    # ADMIN USER LOCK
    # ========================================================

    if not DISCORD_ADMIN_USER_ID:
        return {
            "type": 4,

            "data": {
                "content":
                    "⛔ Discord administrator access "
                    "has not been configured.",

                "flags": 64,
            },
        }


    if (
        discord_user_id
        != DISCORD_ADMIN_USER_ID
    ):
        print(
            "Unauthorized Discord command attempt:",
            discord_user_id,
        )

        return {
            "type": 4,

            "data": {
                "content":
                    "⛔ You are not authorized to access "
                    "HHFD timekeeping.",

                "flags": 64,
            },
        }


    # ========================================================
    # COMMAND
    # ========================================================

    data = payload.get(
        "data",
        {}
    )

    command_name = data.get(
        "name"
    )


    # ========================================================
    # /CORRECTIONS
    # ========================================================

    if command_name == "corrections":

        try:
            rows = (
                load_pending_corrections()
            )

            message = (
                build_corrections_message(
                    rows
                )
            )

        except Exception as exc:

            print(
                "Discord corrections query failed:",
                repr(
                    exc
                ),
            )

            message = (
                "❌ Unable to retrieve "
                "time correction requests."
            )

        return {
            "type": 4,

            "data": {
                "content":
                    message,

                # Ephemeral.
                "flags": 64,
            },
        }


    # ========================================================
    # UNKNOWN COMMAND
    # ========================================================

    return {
        "type": 4,

        "data": {
            "content":
                "Unknown command.",

            "flags": 64,
        },
    }
