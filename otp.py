
import hashlib
import hmac
import logging
import os
import secrets

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BREVO_EMAIL_API_URL = "https://api.brevo.com/v3/smtp/email"


def generate_otp() -> str:
    
    """Return a zero-padded, six-digit one-time password."""
    
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(email: str, otp: str) -> str:
    
    """Return a keyed digest; the email is bound to the OTP."""
    
    secret = os.getenv("OTP_SECRET")

    if not secret:
        raise RuntimeError("OTP_SECRET is not configured")

    normalized_email = email.strip().lower()
    message = f"{normalized_email}:{otp}".encode("utf-8")

    return hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def otp_hash_matches(expected_hash: str, supplied_hash: str) -> bool:
    
    """Compare OTP digests in constant time."""
    
    return hmac.compare_digest(expected_hash, supplied_hash)


def send_otp_email(to_email: str, otp: str) -> None:
    
    """Send a signup verification OTP using the Brevo HTTPS API."""

    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("BREVO_SENDER_EMAIL")
    sender_name = os.getenv(
        "BREVO_SENDER_NAME",
        "K12 Educational Intelligence",
    )

    if not api_key:
        raise RuntimeError("BREVO_API_KEY is not configured")

    if not sender_email:
        raise RuntimeError("BREVO_SENDER_EMAIL is not configured")

    recipient = to_email.strip().lower()

    payload = {
        "sender": {
            "name": sender_name,
            "email": sender_email,
        },
        "to": [
            {
                "email": recipient,
            }
        ],
        
        "subject": "K12 Educational Intelligence | Email Verification",
        
        "textContent": (
            "Hello,\n\n"
            "Thank you for registering with K12 Educational Intelligence.\n\n"
            "To verify your email address and complete your registration, "
            "please use the verification code below:\n\n"
            f"    {otp}\n\n"
            "This verification code is valid for 5 minutes and can only be "
            "used once. Please do not share this code with anyone.\n\n"
            "If you did not initiate this registration, you may safely "
            "ignore this email. No further action is required.\n\n"
            "Regards,\n"
            "K12 Educational Intelligence Team\n\n"
            "---\n"
            "This is an automated message. Please do not reply to this email."
        ),

    }

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }

    try:
        response = requests.post(
            BREVO_EMAIL_API_URL,
            headers=headers,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        logger.info(
            "Brevo accepted verification email; message ID: %s",
            result.get("messageId"),
        )

    except requests.RequestException:
        logger.exception("Signup verification email delivery failed")
        raise

