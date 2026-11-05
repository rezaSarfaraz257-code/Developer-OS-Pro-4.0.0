import base64
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)
RESEND_API_URL = "https://api.resend.com/emails"


class EmailBackend(BaseEmailBackend):
    """Send Django email messages through Resend's HTTPS API (SMTP-free)."""

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.api_key = str(getattr(settings, "RESEND_API_KEY", "") or "").strip()
        self.timeout = max(1, int(getattr(settings, "EMAIL_TIMEOUT", 10)))

    @staticmethod
    def _attachment_payload(attachment):
        if isinstance(attachment, tuple):
            filename, content, content_type = (list(attachment) + [None, None, None])[:3]
            if isinstance(content, str):
                content = content.encode("utf-8")
            if not isinstance(content, bytes):
                raise ValueError("Unsupported email attachment content.")
            return {
                "filename": str(filename or "attachment"),
                "content": base64.b64encode(content).decode("ascii"),
                **({"content_type": content_type} if content_type else {}),
            }

        filename = attachment.get_filename()
        content = attachment.get_payload(decode=True)
        if not filename or content is None:
            raise ValueError("Unsupported email MIME attachment.")
        return {
            "filename": filename,
            "content": base64.b64encode(content).decode("ascii"),
            "content_type": attachment.get_content_type(),
        }

    def _build_payload(self, message):
        sender = str(message.from_email or "").strip()
        recipients = list(message.to or [])
        if not sender or not recipients:
            raise ValueError("Email sender and at least one recipient are required.")

        payload = {
            "from": sender,
            "to": recipients,
            "subject": str(message.subject or ""),
            "text": str(message.body or ""),
        }
        if message.cc:
            payload["cc"] = list(message.cc)
        if message.bcc:
            payload["bcc"] = list(message.bcc)
        if getattr(message, "reply_to", None):
            payload["reply_to"] = list(message.reply_to)

        for alternative in getattr(message, "alternatives", []):
            content = getattr(alternative, "content", alternative[0])
            mimetype = getattr(alternative, "mimetype", alternative[1])
            if mimetype == "text/html":
                payload["html"] = str(content)
                break

        attachments = getattr(message, "attachments", [])
        if attachments:
            payload["attachments"] = [
                self._attachment_payload(item) for item in attachments
            ]
        return payload

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if not self.api_key:
            if self.fail_silently:
                return 0
            raise RuntimeError("Resend API key is not configured.")

        sent = 0
        for message in email_messages:
            try:
                body = json.dumps(
                    self._build_payload(message),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                request = urllib.request.Request(
                    RESEND_API_URL,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "Developer-OS-Mailer/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if not 200 <= int(response.status) < 300:
                        raise RuntimeError(
                            f"Resend API returned HTTP {int(response.status)}."
                        )
                    response.read()
                sent += 1
            except urllib.error.HTTPError as exc:
                logger.warning("Resend API rejected an email (HTTP %s).", exc.code)
                if not self.fail_silently:
                    raise RuntimeError(
                        f"Resend API rejected the email (HTTP {exc.code})."
                    ) from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.warning(
                    "Resend API email request failed (error_type=%s).",
                    type(exc).__name__,
                )
                if not self.fail_silently:
                    raise RuntimeError(
                        "Resend API email request could not be completed."
                    ) from None
            except Exception:
                if not self.fail_silently:
                    raise
        return sent
