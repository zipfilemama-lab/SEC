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

    Поддерживаемые команды:

        /servo help
        /servo 90 90
        /servo center
        /servo off

        servo 90 90
        серво 90 90
        /серво 90 90

    Команда help работает даже тогда, когда PCA9685 не подключена.
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

    @staticmethod
    def normalize_text(text):
        if text is None:
            return ""

        return str(text).strip().lower()

    @staticmethod
    def message_id(message):
        try:
            return int(message.get("id", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def find_command_index(parts):
        allowed_commands = {
            "/servo",
            "servo",
            "/серво",
            "серво",
        }

        for index, part in enumerate(parts):
            # В групповом чате команда может выглядеть:
            # /servo@имя_бота help
            clean_part = part.split("@", 1)[0]

            if clean_part in allowed_commands:
                return index

        return None

    def parse_servo_command(self, text):
        """
        Возвращает один из вариантов:

            ("move", angle_1, angle_2)
            ("center", None, None)
            ("off", None, None)
            ("help", None, None)
            None
        """

        text = self.normalize_text(text)

        if not text:
            return None

        parts = text.split()

        if not parts:
            return None

        command_index = self.find_command_index(parts)

        if command_index is None:
            return None

        parts = parts[command_index:]

        # Убираем имя бота из команды:
        # /servo@security_bot help -> /servo help
        parts[0] = parts[0].split("@", 1)[0]

        if len(parts) == 1:
            return "help", None, None

        action = parts[1]

        if action in {"help", "помощь", "?"}:
            return "help", None, None

        if action in {"center", "centre", "центр"}:
            return "center", None, None

        if action in {
            "off",
            "disable",
            "stop",
            "выкл",
            "выключить",
            "отключить",
        }:
            return "off", None, None

        if len(parts) < 3:
            return "help", None, None

        try:
            angle_1 = int(parts[1])
            angle_2 = int(parts[2])
        except ValueError:
            return "help", None, None

        if not SERVO_MIN_ANGLE <= angle_1 <= SERVO_MAX_ANGLE:
            raise ValueError(
                f"Угол первого сервопривода должен быть "
                f"от {SERVO_MIN_ANGLE} до {SERVO_MAX_ANGLE} градусов"
            )

        if not SERVO_MIN_ANGLE <= angle_2 <= SERVO_MAX_ANGLE:
            raise ValueError(
                f"Угол второго сервопривода должен быть "
                f"от {SERVO_MIN_ANGLE} до {SERVO_MAX_ANGLE} градусов"
            )

        return "move", angle_1, angle_2

    def help_text(self):
        controller_open = getattr(self.servo_controller, "bus", None) is not None
        controller_enabled = getattr(self.servo_controller, "enabled", False)

        if not controller_enabled:
            hardware_status = "отключены настройкой SERVO_ENABLED"
        elif controller_open:
            hardware_status = "PCA9685 подключена и инициализирована"
        else:
            hardware_status = (
                "PCA9685 сейчас не инициализирована; "
                "команда help работает, но движение недоступно"
            )

        return (
            "Команды сервоприводов:\n\n"
            "/servo help — показать эту справку\n"
            "/servo 90 90 — установить углы двух сервоприводов\n"
            "/servo 70 110 — первый на 70°, второй на 110°\n"
            "/servo center — вернуть оба сервопривода в центр\n"
            "/servo off — отключить PWM-сигнал\n\n"
            f"Допустимые углы: {SERVO_MIN_ANGLE}–{SERVO_MAX_ANGLE}°\n"
            f"Пауза между движениями: "
            f"{SERVO_COMMAND_COOLDOWN_SECONDS} сек.\n"
            f"Лимит: {SERVO_MAX_COMMANDS_PER_MINUTE} команд в минуту\n\n"
            f"Состояние оборудования: {hardware_status}"
        )

    def cleanup_old_command_timestamps(self):
        now = time.time()

        while (
            self.command_timestamps
            and now - self.command_timestamps[0] > 60
        ):
            self.command_timestamps.popleft()

    def check_rate_limit(self):
        now = time.time()

        if getattr(self.servo_controller, "busy", False):
            return (
                False,
                "Сервоприводы уже двигаются. Новая команда отклонена.",
            )

        seconds_after_last = now - self.last_servo_command_time

        if seconds_after_last < SERVO_COMMAND_COOLDOWN_SECONDS:
            wait_seconds = int(
                SERVO_COMMAND_COOLDOWN_SECONDS - seconds_after_last
            ) + 1

            return (
                False,
                f"Слишком часто. Подождите ещё примерно "
                f"{wait_seconds} сек.",
            )

        self.cleanup_old_command_timestamps()

        if len(self.command_timestamps) >= SERVO_MAX_COMMANDS_PER_MINUTE:
            return (
                False,
                (
                    "Достигнут лимит сервоприводов: максимум "
                    f"{SERVO_MAX_COMMANDS_PER_MINUTE} команд в минуту. "
                    "Подождите, чтобы сервоприводы и провода не грелись."
                ),
            )

        return True, ""

    def register_accepted_servo_command(self):
        now = time.time()

        self.last_servo_command_time = now
        self.command_timestamps.append(now)

    def safe_send_text(self, message):
        try:
            success = self.tdm_client.send_text_message(message)

            if not success:
                print("[TDM SERVO] TDM не подтвердил отправку ответа")

            return success

        except Exception as error:
            print("[TDM SERVO SEND ERROR]", error)
            return False

    def handle_message(self, message):
        message_id = self.message_id(message)
        text = message.get("message", "")

        if message_id <= 0:
            print("[TDM SERVO] Message without valid ID:", message)
            return

        if message_id <= self.last_processed_message_id:
            return

        print(
            f"[TDM SERVO] Checking message "
            f"id={message_id}, text={text!r}"
        )

        try:
            parsed = self.parse_servo_command(text)

        except ValueError as error:
            self.safe_send_text(
                f"Ошибка команды сервоприводов: {error}"
            )
            self.last_processed_message_id = message_id
            return

        if parsed is None:
            self.last_processed_message_id = message_id
            return

        action, angle_1, angle_2 = parsed

        print(
            f"[TDM SERVO] Servo command from message "
            f"{message_id}: {text}"
        )

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
                self.safe_send_text(
                    f"Ошибка отключения сервоприводов: {error}"
                )

            self.last_processed_message_id = message_id
            return

        # Для движения контроллер должен быть открыт.
        if getattr(self.servo_controller, "bus", None) is None:
            self.safe_send_text(
                "Команда принята, но PCA9685 не инициализирована. "
                "Проверьте I²C, питание платы и вывод команды "
                "'sudo i2cdetect -y 1'."
            )
            self.last_processed_message_id = message_id
            return

        allowed, reason = self.check_rate_limit()

        if not allowed:
            self.safe_send_text(
                f"Команда сервоприводов отклонена: {reason}"
            )
            self.last_processed_message_id = message_id
            return

        self.register_accepted_servo_command()

        if action == "center":
            self.safe_send_text(
                "Принял команду: возвращаю сервоприводы в центр."
            )

            def center_callback(success, result_message):
                self.safe_send_text(result_message)

            self.servo_controller.center_async(
                callback=center_callback
            )

        elif action == "move":
            self.safe_send_text(
                "Принял команду: поворачиваю сервоприводы "
                f"на {angle_1}° и {angle_2}°."
            )

            def move_callback(success, result_message):
                self.safe_send_text(result_message)

            self.servo_controller.move_to_angles_async(
                angle_1,
                angle_2,
                callback=move_callback,
            )

        self.last_processed_message_id = message_id

    def run_forever(self, stop_event):
        """
        Периодически получает непрочитанные сообщения TDM.

        Сообщения обязательно сортируются по возрастанию ID.
        Иначе новое сообщение может помешать обработке более старой
        команды.
        """

        print("[TDM SERVO] Listener started")
        print(
            f"[TDM SERVO] Poll interval: "
            f"{self.poll_interval_seconds} seconds"
        )

        while not stop_event.is_set():
            try:
                messages = self.tdm_client.get_all_unread_messages()

                if messages:
                    messages = sorted(
                        messages,
                        key=self.message_id,
                    )

                    print(
                        f"[TDM SERVO] Received "
                        f"{len(messages)} unread message(s)"
                    )

                    for message in messages:
                        self.handle_message(message)

                    if self.last_processed_message_id > 0:
                        confirmed = self.tdm_client.confirm_messages(
                            self.last_processed_message_id
                        )

                        if not confirmed:
                            print(
                                "[TDM SERVO] Не удалось подтвердить "
                                "обработанные сообщения"
                            )

            except Exception as error:
                print("[TDM SERVO ERROR]", error)

            stop_event.wait(self.poll_interval_seconds)

        print("[TDM SERVO] Listener stopped")
