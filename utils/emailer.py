import html
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid


class EmailSender:
    def __init__(self, host, port, user, password, from_name, from_email, reply_to="", use_ssl=False):
        self.host = str(host or "").strip()
        self.port = int(port or 587)
        self.user = str(user or "").strip()
        self.password = str(password or "")
        self.from_name = str(from_name or "").strip()
        self.from_email = str(from_email or self.user).strip()
        self.reply_to = str(reply_to or "").strip()
        self.use_ssl = bool(use_ssl)
        self.smtp = None

    def connect(self):
        if not self.host or not self.user or not self.password:
            raise ValueError("SMTP host, email and app password are required.")
        if self.use_ssl:
            self.smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
        else:
            self.smtp = smtplib.SMTP(self.host, self.port, timeout=30)
            self.smtp.ehlo()
            self.smtp.starttls()
            self.smtp.ehlo()
        self.smtp.login(self.user, self.password)
        return self

    def verify(self):
        try:
            self.connect()
            self.close()
            return True, "Login successful. SMTP is ready."
        except Exception as exc:
            self.close()
            return False, str(exc)

    def send(self, to_email, subject, body, unsubscribe_email=""):
        if self.smtp is None:
            self.connect()
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self.from_name or self.from_email, self.from_email))
        message["To"] = to_email
        message["Date"] = formatdate(localtime=True)
        domain = self.from_email.split("@")[-1] if "@" in self.from_email else None
        message["Message-ID"] = make_msgid(domain=domain)
        if self.reply_to:
            message["Reply-To"] = self.reply_to
        if unsubscribe_email:
            message["List-Unsubscribe"] = f"<mailto:{unsubscribe_email}?subject=unsubscribe>"
        message.set_content(body)
        message.add_alternative(text_to_html(body), subtype="html")
        self.smtp.send_message(message)

    def close(self):
        if self.smtp is not None:
            try:
                self.smtp.quit()
            except Exception:
                pass
            self.smtp = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        self.close()


def _linkify(escaped_text):
    pattern = r"(https?://[^\s<]+)"
    return re.sub(pattern, r'<a href="\1" style="color:#2563eb;">\1</a>', escaped_text)


def text_to_html(text):
    blocks = [block for block in re.split(r"\n\s*\n", str(text or "").strip()) if block.strip()]
    parts = []
    for block in blocks:
        escaped = html.escape(block).replace("\n", "<br>")
        parts.append(
            f'<p style="margin:0 0 14px 0;">{_linkify(escaped)}</p>'
        )
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.6;color:#1f2937;max-width:620px;">'
        + "".join(parts)
        + "</div>"
    )


def decorate(body, opt_out_line="If this isn't relevant, just reply STOP and I won't follow up."):
    text = str(body or "").strip()
    if opt_out_line and opt_out_line.lower() not in text.lower():
        text = f"{text}\n\n{opt_out_line}"
    return text
