import json
import os
from datetime import timezone
from typing import Optional

import httpx
import psycopg
from fastapi import APIRouter, Header, HTTPException, Request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")

DISCORD_APP_ID = os.getenv("DISCORD_APP_ID")
DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_ADMIN_USER_ID = os.getenv("DISCORD_ADMIN_USER_ID")


def get_connection():
    return psycopg.connect(
        DATABASE_URL,
        autocommit=False,
    )


def verify_discord_signature(
    raw_body: bytes,
    signature: Optional[str],
    timestamp: Optional[str],
):
    if (
        not DISCORD_PUBLIC_KEY
        or not signature
        or not timestamp
    ):
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
            timestamp.encode()
            + raw_body,
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


def load_pending_corrections():
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                select
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
            f"Reason: {reason}"
        )

        lines.append(
            "Submitted: "
            + format_dt(
                requested_at
            )
        )

        lines.append("")

    lines.append(
        "Approve or deny requests in the "
        "Harmony Hills Timekeeping Admin Console."
    )

    message = "\n".join(
        lines
    )

    # Discord message content limit protection.
    if len(message) > 1900:
        message = (
            message[:1850]
            + "\n\n…Additional requests are available "
            + "in the Admin Console."
        )

    return message


async def register_commands():
    if (
        not DISCORD_APP_ID
        or not DISCORD_BOT_TOKEN
    ):
        print(
            "Discord command registration skipped: "
            "missing app ID or bot token."
        )

        return

    url = (
        "https://discord.com/api/v10/applications/"
        + DISCORD_APP_ID
        + "/commands"
    )

payload = {
    "name": "corrections",
    "description": (
        "View pending HHFD time correction requests"
    ),

    # User-installed application
    "integration_types": [
        1
    ],

    # Allow command in bot DMs AND private channels
    "contexts": [
        1,
        2
    ],
}

    headers = {
        "Authorization":
            "Bot "
            + DISCORD_BOT_TOKEN,

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

            response.raise_for_status()

        print(
            "Discord /corrections command registered."
        )

    except Exception as exc:
        print(
            "Discord command registration failed:",
            exc,
        )


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

    payload = json.loads(
        raw_body
    )

    interaction_type = payload.get(
        "type"
    )


    # ========================================================
    # PING
    # ========================================================

    if interaction_type == 1:
        return {
            "type": 1
        }


    # ========================================================
    # APPLICATION COMMAND
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


    data = payload.get(
        "data",
        {}
    )

    command_name = data.get(
        "name"
    )


    # ========================================================
    # IDENTIFY USER
    # ========================================================

    discord_user_id = None

    if payload.get("user"):
        discord_user_id = (
            payload["user"]
            .get("id")
        )

    elif payload.get("member"):
        discord_user_id = (
            payload["member"]
            .get(
                "user",
                {}
            )
            .get("id")
        )


    # ========================================================
    # ADMIN LOCK
    # ========================================================

    if (
        not DISCORD_ADMIN_USER_ID
        or discord_user_id
        != DISCORD_ADMIN_USER_ID
    ):
        return {
            "type": 4,
            "data": {
                "content":
                    "⛔ You are not authorized "
                    "to access HHFD timekeeping.",

                "flags": 64,
            },
        }


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
                exc,
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


    return {
        "type": 4,
        "data": {
            "content":
                "Unknown command.",

            "flags": 64,
        },
    }
