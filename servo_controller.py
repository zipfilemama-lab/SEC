import time
import threading
import smbus

from config import (
    SERVO_ENABLED,
    SERVO_I2C_BUS,
    SERVO_I2C_ADDRESS,
    SERVO_1_CHANNEL,
    SERVO_2_CHANNEL,
    SERVO_CENTER_ANGLE,
    SERVO_MIN_ANGLE,
    SERVO_MAX_ANGLE,
    SERVO_1_ALERT_ANGLE,
    SERVO_2_ALERT_ANGLE,
    SERVO_STEP_DELAY,
    SERVO_HOLD_SECONDS,
    SERVO_AUTO_OFF_AFTER_MOVE,
)


MODE1 = 0x00
MODE2 = 0x01
PRESCALE = 0xFE
LED0_ON_L = 0x06

SERVO_FREQ = 50

# Более мягкий и безопасный диапазон импульса.
# 1000–2000 мкс обычно безопаснее, чем 500–2500 мкс.
MIN_PULSE_US = 1000
MAX_PULSE_US = 2000


class ServoController:
    """
    Управление двумя сервоприводами через PCA9685.

    Raspberry видит плату PCA9685 по I2C.
    Сами сервоприводы не видны как устройства.
    """

    def __init__(self):
        self.enabled = SERVO_ENABLED
        self.bus_number = SERVO_I2C_BUS
        self.address = SERVO_I2C_ADDRESS
        self.bus = None

        self.lock = threading.Lock()
        self.busy = False

        self.current_angle_1 = SERVO_CENTER_ANGLE
        self.current_angle_2 = SERVO_CENTER_ANGLE

    def open(self):
        if not self.enabled:
            print("[SERVO] Disabled in config")
            return

        print("[SERVO] Opening I2C bus...")
        self.bus = smbus.SMBus(self.bus_number)

        self.setup_pca9685()

        print("[SERVO] Moving servos to center")
        self.set_servo_angle(SERVO_1_CHANNEL, SERVO_CENTER_ANGLE)
        self.set_servo_angle(SERVO_2_CHANNEL, SERVO_CENTER_ANGLE)

        self.current_angle_1 = SERVO_CENTER_ANGLE
        self.current_angle_2 = SERVO_CENTER_ANGLE

        time.sleep(0.5)

        if SERVO_AUTO_OFF_AFTER_MOVE:
            self.disable_all()

        print("[SERVO] Ready")

    def close(self):
        if self.bus is None:
            return

        try:
            print("[SERVO] Returning servos to center")
            self.set_servo_angle(SERVO_1_CHANNEL, SERVO_CENTER_ANGLE)
            self.set_servo_angle(SERVO_2_CHANNEL, SERVO_CENTER_ANGLE)
            time.sleep(0.5)

            self.disable_all()

        except Exception as error:
            print("[SERVO CLOSE ERROR]", error)

        try:
            self.bus.close()
        except Exception:
            pass

        self.bus = None
        print("[SERVO] Closed")

    def write(self, reg, val):
        self.bus.write_byte_data(self.address, reg, val)

    def read(self, reg):
        return self.bus.read_byte_data(self.address, reg)

    def setup_pca9685(self):
        """
        Инициализация PCA9685 на 50 Гц.
        """
        self.write(MODE1, 0x00)
        time.sleep(0.01)

        self.write(MODE2, 0x04)

        oldmode = self.read(MODE1)
        sleep_mode = (oldmode & 0x7F) | 0x10

        self.write(MODE1, sleep_mode)

        # 121 примерно даёт 50 Гц при стандартном генераторе PCA9685 25 МГц.
        self.write(PRESCALE, 121)

        self.write(MODE1, oldmode)
        time.sleep(0.01)

        # Restart + auto-increment.
        self.write(MODE1, oldmode | 0xA1)
        time.sleep(0.01)

    def pulse_to_count(self, pulse_us):
        """
        Перевод микросекунд в значение PCA9685 0–4095.
        """
        period_us = 1_000_000 / SERVO_FREQ
        return int(pulse_us * 4096 / period_us)

    def set_pwm(self, channel, on, off):
        if self.bus is None:
            raise RuntimeError("ServoController не открыт. Сначала вызови open().")

        reg = LED0_ON_L + 4 * channel

        self.write(reg, on & 0xFF)
        self.write(reg + 1, on >> 8)
        self.write(reg + 2, off & 0xFF)
        self.write(reg + 3, off >> 8)

    def clamp_angle(self, angle):
        angle = int(angle)
        return max(SERVO_MIN_ANGLE, min(SERVO_MAX_ANGLE, angle))

    def set_servo_angle(self, channel, angle):
        angle = self.clamp_angle(angle)

        pulse = MIN_PULSE_US + (angle / 180) * (MAX_PULSE_US - MIN_PULSE_US)
        pulse = int(pulse)

        count = self.pulse_to_count(pulse)

        self.set_pwm(channel, 0, count)

        print(
            f"[SERVO] channel={channel}, angle={angle}, "
            f"pulse={pulse}, count={count}"
        )

    def disable_servo(self, channel):
        if self.bus is None:
            return

        self.set_pwm(channel, 0, 0)
        print(f"[SERVO] channel={channel} disabled")

    def disable_all(self):
        if self.bus is None:
            return

        self.disable_servo(SERVO_1_CHANNEL)
        self.disable_servo(SERVO_2_CHANNEL)

    def smooth_move(self, channel, start_angle, end_angle):
        start_angle = self.clamp_angle(start_angle)
        end_angle = self.clamp_angle(end_angle)

        if start_angle == end_angle:
            self.set_servo_angle(channel, end_angle)
            return

        step = 2 if end_angle > start_angle else -2

        for angle in range(start_angle, end_angle, step):
            self.set_servo_angle(channel, angle)
            time.sleep(SERVO_STEP_DELAY)

        self.set_servo_angle(channel, end_angle)

    def move_to_angles(self, angle_1, angle_2):
        """
        Ручное управление из TDM:
        /servo 90 120
        """
        if not self.enabled:
            return False, "Сервоприводы отключены в config/.env"

        if self.bus is None:
            return False, "ServoController не открыт"

        angle_1 = self.clamp_angle(angle_1)
        angle_2 = self.clamp_angle(angle_2)

        with self.lock:
            if self.busy:
                return False, "Сервоприводы уже двигаются. Команда отклонена."

            self.busy = True

        try:
            print(f"[SERVO] TDM command: servo1={angle_1}, servo2={angle_2}")

            self.smooth_move(SERVO_1_CHANNEL, self.current_angle_1, angle_1)
            self.smooth_move(SERVO_2_CHANNEL, self.current_angle_2, angle_2)

            self.current_angle_1 = angle_1
            self.current_angle_2 = angle_2

            if SERVO_AUTO_OFF_AFTER_MOVE:
                time.sleep(0.3)
                self.disable_all()

            return True, f"Сервоприводы повернуты: 1={angle_1}°, 2={angle_2}°"

        except Exception as error:
            print("[SERVO COMMAND ERROR]", error)
            return False, f"Ошибка сервоприводов: {error}"

        finally:
            with self.lock:
                self.busy = False

    def move_to_angles_async(self, angle_1, angle_2, callback=None):
        """
        Запускает поворот в отдельном потоке, чтобы камера не зависала.
        """

        def worker():
            success, message = self.move_to_angles(angle_1, angle_2)

            if callback is not None:
                try:
                    callback(success, message)
                except Exception as error:
                    print("[SERVO CALLBACK ERROR]", error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def center(self):
        return self.move_to_angles(SERVO_CENTER_ANGLE, SERVO_CENTER_ANGLE)

    def center_async(self, callback=None):
        self.move_to_angles_async(SERVO_CENTER_ANGLE, SERVO_CENTER_ANGLE, callback)

    def alert_motion(self):
        """
        Автоматическая реакция на камеру.
        Сейчас в main.py она НЕ вызывается.
        Оставляем на будущее.
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

            self.current_angle_1 = SERVO_CENTER_ANGLE
            self.current_angle_2 = SERVO_CENTER_ANGLE

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

            self.current_angle_1 = SERVO_CENTER_ANGLE
            self.current_angle_2 = SERVO_CENTER_ANGLE

            if SERVO_AUTO_OFF_AFTER_MOVE:
                time.sleep(0.3)
                self.disable_all()

            print("[SERVO] Motion alert sequence finished")

        except Exception as error:
            print("[SERVO ERROR]", error)

        finally:
            with self.lock:
                self.busy = False

    def alert_motion_async(self):
        if not self.enabled:
            return

        thread = threading.Thread(
            target=self.alert_motion,
            daemon=True,
        )
        thread.start()
