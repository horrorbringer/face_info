import requests
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Test Telegram Bot token configuration and optionally send a test message to a Chat ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chat-id",
            type=str,
            help="Optional Telegram Chat ID or handle to send a test message to.",
        )
        parser.add_argument(
            "--token",
            type=str,
            help="Optional Telegram bot token override (defaults to settings.TELEGRAM_BOT_TOKEN).",
        )

    def handle(self, *args, **options):
        token = options.get("token") or getattr(settings, "TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = options.get("chat_id")

        self.stdout.write(self.style.NOTICE("===> Testing Telegram Bot Configuration..."))

        if not token:
            self.stdout.write(
                self.style.ERROR(
                    "✗ Error: No TELEGRAM_BOT_TOKEN configured!\n"
                    "  Please set TELEGRAM_BOT_TOKEN in your .env file or pass --token <your_bot_token>."
                )
            )
            return

        # 1. Test getMe to verify bot credentials
        get_me_url = f"https://api.telegram.org/bot{token}/getMe"
        try:
            resp = requests.get(get_me_url, timeout=10)
            data = resp.json()
            if not data.get("ok"):
                self.stdout.write(
                    self.style.ERROR(f"✗ Telegram API Error: {data.get('description', 'Unknown error')}")
                )
                return

            bot_info = data.get("result", {})
            username = bot_info.get("username", "Unknown")
            name = bot_info.get("first_name", "Bot")
            self.stdout.write(self.style.SUCCESS(f"✓ Bot Connected: @{username} ({name})"))
        except requests.exceptions.RequestException as e:
            self.stdout.write(self.style.ERROR(f"✗ Network error connecting to Telegram: {e}"))
            return

        # 2. If chat-id is provided, send test alert
        if chat_id:
            self.stdout.write(f"Sending test notification to Chat ID: {chat_id}...")
            send_url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": (
                    "🔔 <b>Smart Attendance System — Test Notification</b>\n\n"
                    "✓ Your Telegram Bot is successfully configured and connected to the attendance system.\n"
                    "Guardian absence notices and alerts will arrive here."
                ),
                "parse_mode": "HTML",
            }
            try:
                send_resp = requests.post(send_url, json=payload, timeout=10)
                send_data = send_resp.json()
                if send_data.get("ok"):
                    msg_id = send_data.get("result", {}).get("message_id")
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ Test message delivered successfully! (Message ID: {msg_id})"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"✗ Telegram send failed: {send_data.get('description', 'Unknown error')}\n"
                            "  Tip: If sending to a user, make sure the user clicked 'Start' in the bot first."
                        )
                    )
            except requests.exceptions.RequestException as e:
                self.stdout.write(self.style.ERROR(f"✗ Network error sending message: {e}"))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "ℹ No --chat-id specified. Pass --chat-id <your_id> to send a test message."
                )
            )
