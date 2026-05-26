import time
import threading
from smbus2 import SMBus

from config import (
    SERVO_ENABLED,
    SERVO_I2C_BUS,
    SERVO_I2C_ADDRESS,
    SERVO_1_CHANNEL,
    SERVO_2_CHANNEL,
    SERVO_CENTER_ANGLE,
    SERVO_1_ALERT_ANGLE,
    SERVO_2_ALERT_ANGLE,
    SERVO_STEP_DELAY,
    SERVO_HOLD_SECONDS,
)

MODE1 = 0x00
MODE2 = 0x01
PRESCALE = 0xFE
LED0_ON_L = 0x06

SERVO_FREQ = 50

# Безопасный диапазон для MG996R.
# Если нужно больше амплитуда, можно потом расширить.
MIN_PULSE = 1000
MAX_PULSE = 2000


class ServoController:
    """
    Управляет двумя сервоприводами через PCA9685.

    Канал 0 = первый сервопривод
    Канал 1 = второй сервопривод
    """

    def __init__(self):
        self.enabled = SERVO_ENABLED
        self.bus_number = SERVO_I2C_BUS
        self.address = SERVO_I2C_ADDRESS
        self.bus = None
        self.lock = threading.Lock()
        self.busy = False

    def open(self):
        if not self.enabled:
            print("[SERVO] Disabled in config")
            return

        print("[SERVO] Opening I2C bus...")
        self.bus = SMBus(self.bus_number)
        self.setup_pca9685()

        print("[SERVO] Moving servos to center")
        self.set_servo_angle(SERVO_1_CHANNEL, SERVO_CENTER_ANGLE)
        self.set_servo_angle(SERVO_2_CHANNEL, SERVO_CENTER_ANGLE)
        time.sleep(0.5)

        print("[SERVO] Ready")

    def close(self):
        if self.bus is None:
            return

        try:
            print("[SERVO] Returning servos to center")
            self.set_servo_angle(SERVO_1_CHANNEL, SERVO_CENTER_ANGLE)
            self.set_servo_angle(SERVO_2_CHANNEL, SERVO_CENTER_ANGLE)
            time.sleep(0.5)

            self.disable_servo(SERVO_1_CHANNEL)
            self.disable_servo(SERVO_2_CHANNEL)
        except Exception as error:
            print("[SERVO CLOSE ERROR]", error)

        self.bus.close()
        self.bus = None
        print("[SERVO] Closed")

    def write(self, reg, val):
        self.bus.write_byte_data(self.address, reg, val)

    def read(self, reg):
        return self.bus.read_byte_data(self.address, reg)

    def setup_pca9685(self):
        """
        Рабочая инициализация из pwm_raw_test.py.
        Важная строка: MODE2 = 0x04.
        Без неё у тебя старый код не крутил сервопривод.
        """
        self.write(MODE1, 0x00)
        time.sleep(0.01)

        # Важно: нормальный выходной режим
        self.write(MODE2, 0x04)

        oldmode = self.read(MODE1)
        sleep_mode = (oldmode & 0x7F) | 0x10

        self.write(MODE1, sleep_mode)
        self.write(PRESCALE, 121)  # примерно 50 Hz
        self.write(MODE1, oldmode)

        time.sleep(0.01)

        # Auto-increment + restart
        self.write(MODE1, oldmode | 0xA1)
        time.sleep(0.01)

    def pulse_to_count(self, pulse_us):
        period_us = 1_000_000 / SERVO_FREQ
        return int(pulse_us * 4096 / period_us)

    def set_pwm(self, channel, on, off):
        reg = LED0_ON_L + 4 * channel

        self.write(reg, on & 0xFF)
        self.write(reg + 1, on >> 8)
        self.write(reg + 2, off & 0xFF)
        self.write(reg + 3, off >> 8)

    def set_servo_angle(self, channel, angle):
        angle = max(0, min(180, angle))

        pulse = MIN_PULSE + (angle / 180) * (MAX_PULSE - MIN_PULSE)
        pulse = int(pulse)

        count = self.pulse_to_count(pulse)

        self.set_pwm(channel, 0, count)

        print(f"[SERVO] channel={channel}, angle={angle}, pulse={pulse}, count={count}")

    def disable_servo(self, channel):
        self.set_pwm(channel, 0, 0)
        print(f"[SERVO] channel={channel} disabled")

    def smooth_move(self, channel, start_angle, end_angle):
        """
        Плавное движение, чтобы MG996R не дёргался резко.
        """
        if start_angle == end_angle:
            self.set_servo_angle(channel, end_angle)
            return

        step = 2 if end_angle > start_angle else -2

        for angle in range(start_angle, end_angle, step):
            self.set_servo_angle(channel, angle)
            time.sleep(SERVO_STEP_DELAY)

        self.set_servo_angle(channel, end_angle)

    def alert_motion(self):
        """
        Боевой сценарий на сработку камеры.

        Логика:
        1. оба в центр
        2. первый моторчик поворачивается
        3. первый возвращается
        4. второй моторчик поворачивается
        5. второй возвращается
        """
        if not self.enabled:
            return

        if self.bus is None:
            print("[SERVO] Not opened")
            return

        with self.lock:
            if self.busy:
                print("[SERVO] Already moving, skip")
                return

            self.busy = True

        try:
            print("[SERVO] Motion alert sequence started")

            self.set_servo_angle(SERVO_1_CHANNEL, SERVO_CENTER_ANGLE)
            self.set_servo_angle(SERVO_2_CHANNEL, SERVO_CENTER_ANGLE)
            time.sleep(0.2)

            self.smooth_move(
                SERVO_1_CHANNEL,
                SERVO_CENTER_ANGLE,
                SERVO_1_ALERT_ANGLE,
            )
            time.sleep(SERVO_HOLD_SECONDS)

            self.smooth_move(
                SERVO_1_CHANNEL,
                SERVO_1_ALERT_ANGLE,
                SERVO_CENTER_ANGLE,
            )
            time.sleep(0.2)

            self.smooth_move(
                SERVO_2_CHANNEL,
                SERVO_CENTER_ANGLE,
                SERVO_2_ALERT_ANGLE,
            )
            time.sleep(SERVO_HOLD_SECONDS)

            self.smooth_move(
                SERVO_2_CHANNEL,
                SERVO_2_ALERT_ANGLE,
                SERVO_CENTER_ANGLE,
            )

            print("[SERVO] Motion alert sequence finished")

        except Exception as error:
            print("[SERVO ERROR]", error)

        finally:
            with self.lock:
                self.busy = False

    def alert_motion_async(self):
        """
        Запускает движение в отдельном потоке,
        чтобы камера и отправка фото не зависали.
        """
        if not self.enabled:
            return

        thread = threading.Thread(
            target=self.alert_motion,
            daemon=True,
        )
        thread.start()
