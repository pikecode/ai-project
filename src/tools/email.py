"""Email Tool."""

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from jinja2 import Template

from .base import BaseTool, registry


class EmailTool(BaseTool):
    """Tool for sending emails."""

    name = "send_email"
    description = "Send an email to specified recipients with subject and body"
    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address(es), comma-separated for multiple",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line",
            },
            "body": {
                "type": "string",
                "description": "Email body content (HTML or plain text)",
            },
            "template": {
                "type": "string",
                "description": "Optional template name to use",
            },
            "template_vars": {
                "type": "object",
                "description": "Variables for template rendering",
            },
        },
        "required": ["to", "subject", "body"],
    }

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_addr: str,
        use_tls: bool = True,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.use_tls = use_tls
        self._templates: dict[str, Template] = {}

    def register_template(self, name: str, content: str) -> None:
        """Register a Jinja2 template."""
        self._templates[name] = Template(content)

    async def execute(
        self,
        to: str,
        subject: str,
        body: str,
        template: str | None = None,
        template_vars: dict | None = None,
    ) -> dict[str, Any]:
        """Send email to recipients."""
        try:
            # Render template if specified
            if template and template in self._templates:
                template_vars = template_vars or {}
                body = self._templates[template].render(**template_vars)

            # Parse recipients
            recipients = [r.strip() for r in to.split(",")]

            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_addr
            msg["To"] = to

            # Attach HTML content
            msg.attach(MIMEText(body, "html", "utf-8"))

            # Send email
            loop = asyncio.get_event_loop()

            def _send():
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    if self.use_tls:
                        server.starttls()
                    server.login(self.username, self.password)
                    server.send_message(msg)
                return True

            await asyncio.wait_for(
                loop.run_in_executor(None, _send),
                timeout=30.0,
            )

            return {
                "success": True,
                "message": f"Email sent to {len(recipients)} recipient(s)",
                "recipients": recipients,
            }
        except TimeoutError:
            return {"success": False, "error": "Email send timed out"}
        except Exception as e:
            return {"success": False, "error": f"Failed to send email: {e!s}"}


def init_email_tool(
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    from_addr: str,
    use_tls: bool = True,
) -> EmailTool:
    """Initialize and register email tool."""
    tool = EmailTool(smtp_host, smtp_port, username, password, from_addr, use_tls)
    registry.register(tool)
    return tool
