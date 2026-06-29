import time
from collections import deque

from config import (
    SERVO_MIN_ANGLE,
    SERVO_MAX_ANGLE,
    SERVO_COMMAND_COOLDOWN_SECONDS,
    SERVO_MAX_COMMANDS_PER_MINUTE,
)


class TDMServoCommandListener:
    """
    Читает команды из TDM и управляет сервоприводами.

    Команды:
    /servo 90 120
    servo 90 120
    серво 90 120

    /servo center
    /servo off
    /servo help

    Защита:
    - не принимает команду, если серво уже двигаются;
    - ограничивает частоту команд;
    - ограничивает число команд в минуту.
    """

    def __init__(
        self,
        tdm_client,
        servo_controller,
        poll_interval_seconds=5,
    ):
        self.tdm_client = tdm_client
        self.servo_controller = servo_controller
        self.poll_interval_seconds = poll_interval_seconds

        self.last_processed_message_id = 0
        self.last_servo_command_time = 0.0
        self.command_timestamps = deque()

    def normalize_text(self, text):
        if text is None:
            return ""

        return str(text).strip().lower()

    def find_command_index(self, parts):
        allowed_commands = {
            "/servo",
            "servo",
            "/серво",
            "серво",
        }

        for index, part in enumerate(parts):
            if part in allowed_commands:
                return index

        return None

    def parse_servo_command(self, text):
        """
        Возвращает:
        ("move", angle1, angle2)
        ("center", None, None)
        ("off", None, None)
        ("help", None, None)
        None
        """
        text = self.normalize_text(text)

        if not text:
            return None

        # На случай упоминаний в групповом чате.
        text = text.replace("@", " ")
        parts = text.split()

        if not parts:
            return None

        command_index = self.find_command_index(parts)

        if command_index is None:
            return None

        parts = parts[command_index:]

        if len(parts) == 1:
            return ("help", None, None)

        action = parts[1]

        if action in {"help", "помощь", "?"}:
            return ("help", None, None)

        if action in {"center", "centre", "центр"}:
            return ("center", None, None)

        if action in {"off", "disable", "stop", "выкл", "отключить"}:
            return ("off", None, None)

        if len(parts) < 3:
            return ("help", None, None)

        try:
            angle_1 = int(parts[1])
            angle_2 = int(parts[2])
        except ValueError:
            return ("help", None, None)

        if (
            angle_1 < SERVO_MIN_ANGLE
            or angle_1 > SERVO_MAX_ANGLE
            or angle_2 < SERVO_MIN_ANGLE
            or angle_2 > SERVO_MAX_ANGLE
        ):
            raise ValueError(
                f"Углы должны быть от {SERVO_MIN_ANGLE} до {SERVO_MAX_ANGLE} градусов"
            )

        return ("move", angle_1, angle_2)

    def help_text(self):
        return (
            "Команды сервоприводов:\n\n"
            "/servo 90 90 — повернуть оба сервопривода\n"
            "/servo 70 110 — первый на 70°, второй на 110°\n"
            "/servo center — вернуть оба в центр\n"
            "/servo off — отключить PWM-сигнал\n\n"
            "Ограничения безопасности:\n"
            f"углы: {SERVO_MIN_ANGLE}–{SERVO_MAX_ANGLE}°\n"
            f"не чаще 1 команды в {SERVO_COMMAND_COOLDOWN_SECONDS} сек.\n"
            f"максимум {SERVO_MAX_COMMANDS_PER_MINUTE} команд в минуту."
        )

    def cleanup_old_command_timestamps(self):
        now = time.time()

        while self.command_timestamps and now - self.command_timestamps[0] > 60:
            self.command_timestamps.popleft()

    def check_rate_limit(self):
        """
        Проверяет, можно ли принять новую команду движения.
        """
        now = time.time()

        if getattr(self.servo_controller, "busy", False):
            return (
                False,
                "Сервоприводы уже двигаются. Новая команда отклонена.",
            )

        seconds_after_last = now - self.last_servo_command_time

        if seconds_after_last < SERVO_COMMAND_COOLDOWN_SECONDS:
            wait_seconds = int(SERVO_COMMAND_COOLDOWN_SECONDS - seconds_after_last) + 1
            return (
                False,
                f"Слишком часто. Подожди ещё примерно {wait_seconds} сек.",
            )

        self.cleanup_old_command_timestamps()

        if len(self.command_timestamps) >= SERVO_MAX_COMMANDS_PER_MINUTE:
            return (
                False,
                (
                    f"Лимит сервоприводов: максимум "
                    f"{SERVO_MAX_COMMANDS_PER_MINUTE} команд в минуту. "
                    "Подожди, чтобы провода и сервоприводы не грелись."
                ),
            )

        return True, ""

    def register_accepted_servo_command(self):
        now = time.time()
        self.last_servo_command_time = now
        self.command_timestamps.append(now)

    def safe_send_text(self, message):
        try:
            self.tdm_client.send_text_message(message)
        except Exception as error:
            print("[TDM SERVO SEND ERROR]", error)

    def handle_message(self, message):
        message_id = int(message.get("id", 0))
        text = message.get("message", "")

        if message_id <= self.last_processed_message_id:
            return

        try:
            parsed = self.parse_servo_command(text)

        except ValueError as error:
            self.safe_send_text(f"Ошибка команды сервоприводов: {error}")
            self.last_processed_message_id = message_id
            return

        # Важно: подтверждаем даже не-командные сообщения,
        # чтобы они не приходили снова и снова.
        if parsed is None:
            self.last_processed_message_id = message_id
            return

        action, angle_1, angle_2 = parsed

        print(f"[TDM SERVO] command from message {message_id}: {text}")

        if action == "help":
            self.safe_send_text(self.help_text())
            self.last_processed_message_id = message_id
            return

        if action == "off":
            try:
                self.servo_controller.disable_all()
                self.safe_send_text(
                    "PWM-сигнал сервоприводов отключён. "
                    "Сервоприводы не должны удерживать позицию и греться."
                )

            except Exception as error:
                self.safe_send_text(f"Ошибка отключения сервоприводов: {error}")

            self.last_processed_message_id = message_id
            return

        # center и move — это движение, значит проверяем лимиты.
        allowed, reason = self.check_rate_limit()

        if not allowed:
            self.safe_send_text(f"Команда сервоприводов отклонена: {reason}")
            self.last_processed_message_id = message_id
            return

        self.register_accepted_servo_command()

        if action == "center":
            self.safe_send_text("Принял команду: возвращаю сервоприводы в центр.")

            def callback(success, result_message):
                self.safe_send_text(result_message)

            self.servo_controller.center_async(callback=callback)

        elif action == "move":
            self.safe_send_text(
                f"Принял команду: поворачиваю сервоприводы на {angle_1}° и {angle_2}°."
            )

            def callback(success, result_message):
                self.safe_send_text(result_message)

            self.servo_controller.move_to_angles_async(
                angle_1,
                angle_2,
                callback=callback,
            )

        self.last_processed_message_id = message_id

    def run_forever(self, stop_event):
        """
        Простой тестовый вариант: опрашиваем непрочитанные сообщения.
        Интервал специально не маленький, чтобы не спамить API.
        """
        print("[TDM SERVO] Listener started")

        while not stop_event.is_set():
            try:
                messages = self.tdm_client.get_all_unread_messages()

                for message in messages:
                    self.handle_message(message)

                if self.last_processed_message_id > 0:
                    self.tdm_client.confirm_messages(self.last_processed_message_id)

            except Exception as error:
                print("[TDM SERVO ERROR]", error)

            stop_event.wait(self.poll_interval_seconds)

        print("[TDM SERVO] Listener stopped")
