
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


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
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def otp_hash_matches(expected_hash: str, supplied_hash: str) -> bool:
    """Compare OTP digests in constant time."""
    return hmac.compare_digest(expected_hash, supplied_hash)


def send_otp_email(to_email: str, otp: str) -> None:
    """Send a verification OTP using SMTP configuration from environment."""
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or username
    port_text = os.getenv("SMTP_PORT", "587")

    if not host or not username or not password or not sender:
        raise RuntimeError("SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM must be configured")

    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError("SMTP_PORT must be an integer") from exc

    mail = EmailMessage()
    mail["Subject"] = "Verify your K12 account"
    mail["From"] = sender
    mail["To"] = to_email
    mail.set_content(
        f"Your K12 signup verification code is: {otp}\n\n"
        "This code expires in 5 minutes.\n"
        "If you did not request this, you can ignore this email."
    )

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(username, password)
        smtp.send_message(mail)

    logger.info("Signup verification email sent")
