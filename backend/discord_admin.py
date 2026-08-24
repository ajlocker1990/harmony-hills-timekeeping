async def register_commands():
    if not DISCORD_APP_ID or not DISCORD_BOT_TOKEN:
        print(
            "Discord command registration skipped: "
            "missing DISCORD_APP_ID or DISCORD_BOT_TOKEN."
        )
        return

    url = (
        f"https://discord.com/api/v10/applications/"
        f"{DISCORD_APP_ID}/commands"
    )

    payload = {
        "name": "corrections",
        "description": "View pending HHFD time correction requests",
        "integration_types": [1],
        "contexts": [1, 2],
    }

    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )

        print(
            "Discord command registration status:",
            response.status_code,
        )

        print(
            "Discord command registration response:",
            response.text,
        )

        response.raise_for_status()

        print(
            "Discord /corrections command registered."
        )

    except Exception as exc:
        print(
            "Discord command registration failed:",
            repr(exc),
        )
