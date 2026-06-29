import time
from collections import deque

from config import (
    SERVO_COMMAND_COOLDOWN_SECONDS,
    SERVO_MAX_COMMANDS_PER_MINUTE,
)


class TDMServoCommandListener:
    """
    Читает команды из TDM и управляет сервоприводами.

    Защита от перегрева и спама:
    1. Не принимает новую команду слишком часто.
    2. Ограничивает число команд за минуту.
    3. Не ставит команды в очередь, если сервоприводы уже заняты.

    Поддерживаемые команды:

    /servo 90 120
    servo 90 120
    серво 90 120

    /servo center
    /servo off
    /servo help
    """

    def __init__(
        self,
        tdm_client,
        servo_controller,
        poll_interval_seconds=3,
    ):
        self.tdm_client = tdm_client
        self.servo_controller = servo_controller
        self.poll_interval_seconds = poll_interval_seconds

        self.last_processed_message_id = 0

        # Время последней реально принятой команды движения
        self.last_servo_command_time = 0.0

        # История принятых команд за последнюю минуту
        self.command_timestamps = deque()

    def normalize_text(self, text):
        if text is None:
            return ""

        return text.strip().lower()

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

        text = text.replace("@", " ")
        parts = text.split()

        if not parts:
            return None

        command = parts[0]

        allowed_commands = {
            "/servo",
            "servo",
            "/серво",
            "серво",
        }

        if command not in allowed_commands:
            return None

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

        if angle_1 < 0 or angle_1 > 180 or angle_2 < 0 or angle_2 > 180:
            raise ValueError("Углы должны быть от 0 до 180 градусов")

        return ("move", angle_1, angle_2)

    def help_text(self):
        return (
            "Команды сервоприводов:\n\n"
            "/servo 90 90 — повернуть оба сервопривода\n"
            "/servo 70 110 — первый на 70°, второй на 110°\n"
            "/servo center — вернуть оба в центр\n"
            "/servo off — отключить PWM-сигнал\n\n"
            f"Ограничения безопасности:\n"
            f"не чаще 1 команды в {SERVO_COMMAND_COOLDOWN_SECONDS} сек.\n"
            f"максимум {SERVO_MAX_COMMANDS_PER_MINUTE} команд в минуту.\n\n"
            "Углы: от 0 до 180 градусов."
        )

    def cleanup_old_command_timestamps(self):
        """
        Удаляет из истории команды старше 60 секунд.
        """
        now = time.time()

        while self.command_timestamps and now - self.command_timestamps[0] > 60:
            self.command_timestamps.popleft()

    def check_rate_limit(self):
        """
        Проверяет, можно ли сейчас принять новую команду движения.

        Возвращает:
        (True, "")
        или
        (False, "причина")
        """
        now = time.time()

        # 1. Если сервоприводы уже заняты — не принимаем новую команду
        if getattr(self.servo_controller, "busy", False):
            return (
                False,
                "Сервоприводы сейчас уже двигаются. "
                "Новая команда отклонена, чтобы не перегревать питание.",
            )

        # 2. Cooldown между командами
        seconds_after_last = now - self.last_servo_command_time

        if seconds_after_last < SERVO_COMMAND_COOLDOWN_SECONDS:
            wait_seconds = int(SERVO_COMMAND_COOLDOWN_SECONDS - seconds_after_last) + 1
            return (
                False,
                f"Слишком часто. Подожди ещё примерно {wait_seconds} сек.",
            )

        # 3. Лимит команд за минуту
        self.cleanup_old_command_timestamps()

        if len(self.command_timestamps) >= SERVO_MAX_COMMANDS_PER_MINUTE:
            return (
                False,
                f"Лимит сервоприводов: максимум "
                f"{SERVO_MAX_COMMANDS_PER_MINUTE} команд в минуту. "
                "Подожди, чтобы провода и сервоприводы не грелись.",
            )

        return True, ""

    def register_accepted_servo_command(self):
        """
        Запоминает, что команда движения принята.
        """
        now = time.time()
        self.last_servo_command_time = now
        self.command_timestamps.append(now)

    def handle_message(self, message):
        message_id = int(message.get("id", 0))
        text = message.get("message", "")

        if message_id <= self.last_processed_message_id:
            return

        parsed = None

        try:
            parsed = self.parse_servo_command(text)
        except ValueError as error:
            self.tdm_client.send_text_message(f"Ошибка команды сервоприводов: {error}")
            self.last_processed_message_id = message_id
            return

        if parsed is None:
            return

        action, angle_1, angle_2 = parsed

        print(f"[TDM SERVO] command from message {message_id}: {text}")

        # help и off не считаем опасным движением
        if action == "help":
            self.tdm_client.send_text_message(self.help_text())
            self.last_processed_message_id = message_id
            return

        if action == "off":
            try:
                self.servo_controller.disable_all()
                self.tdm_client.send_text_message(
                    "PWM-сигнал сервоприводов отключён. "
                    "Сервоприводы не должны удерживать позицию и греться."
                )
            except Exception as error:
                self.tdm_client.send_text_message(
                    f"Ошибка отключения сервоприводов: {error}"
                )

            self.last_processed_message_id = message_id
            return

        # center и move — это движение, поэтому проверяем лимиты
        allowed, reason = self.check_rate_limit()

        if not allowed:
            self.tdm_client.send_text_message(f"Команда сервоприводов отклонена: {reason}")
            self.last_processed_message_id = message_id
            return

        self.register_accepted_servo_command()

        if action == "center":
            self.tdm_client.send_text_message("Принял команду: возвращаю сервоприводы в центр.")

            def callback(success, result_message):
                self.tdm_client.send_text_message(result_message)

            self.servo_controller.center_async(callback=callback)

        elif action == "move":
            self.tdm_client.send_text_message(
                f"Принял команду: поворачиваю сервоприводы на {angle_1}° и {angle_2}°."
            )

            def callback(success, result_message):
                self.tdm_client.send_text_message(result_message)

            self.servo_controller.move_to_angles_async(
                angle_1,
                angle_2,
                callback=callback,
            )

        self.last_processed_message_id = message_id

    def run_forever(self, stop_event):
        """
        Временный простой вариант: опрашиваем непрочитанные сообщения.
        Для боевого варианта лучше перейти на SSE.
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
