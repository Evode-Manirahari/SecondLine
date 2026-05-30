#
# SecondLine — Twilio SMS helper.
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""Outbound SMS via the Twilio REST API (async, aiohttp).

Used to text the owner a structured summary of each call and to text the
customer details they asked for ("text me the address"). Degrades gracefully:
if Twilio creds are missing (e.g. local eval runs), it logs a simulated send to
the DB instead of failing the call.
"""

from __future__ import annotations

import os

import aiohttp
from loguru import logger

import backend


async def send_sms(to_number: str, body: str) -> dict:
    """Send an SMS. Returns {ok, sid|reason}. Never raises into the pipeline."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")

    if not (account_sid and auth_token and from_number and to_number):
        logger.warning(f"[sms] creds/number missing — simulating send to {to_number}")
        backend.log_sms(to_number or "?", body, "simulated")
        return {"ok": True, "simulated": True}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = {"To": to_number, "From": from_number, "Body": body}
    auth = aiohttp.BasicAuth(account_sid, auth_token)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, auth=auth) as resp:
                payload = await resp.json()
                if resp.status >= 400:
                    logger.error(f"[sms] Twilio error {resp.status}: {payload}")
                    backend.log_sms(to_number, body, f"error:{resp.status}")
                    return {"ok": False, "reason": payload.get("message", "send failed")}
                backend.log_sms(to_number, body, "sent")
                return {"ok": True, "sid": payload.get("sid")}
    except Exception as e:
        logger.error(f"[sms] exception: {e}")
        backend.log_sms(to_number, body, f"exception")
        return {"ok": False, "reason": str(e)}
