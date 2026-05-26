import queue
import threading
import time
from datetime import datetime
from servo_controller import ServoController

from config import (
    ensure_project_dirs,
    validate_config,
    SEND_COOLDOWN_SECONDS,
    WIFI_INTERFACE,
    WIFI_SCAN_INTERVAL_SECONDS,
)

from camera_motion import CameraMotionDetector
from tdm_client import TDMClient

from wifi_scanner import WiFiScannerReporter

from servo_controller import ServoController


send_queue = queue.Queue(maxsize=5)
stop_event = threading.Event()
servo_controller = ServoController()

def read_cpu_temp() -> float | None:
    """
    Читает температуру CPU Raspberry Pi.

    Raspberry хранит температуру здесь:
    /sys/class/thermal/thermal_zone0/temp

    Пример:
    48000 = 48.0 °C
    """
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as file:
            raw_value = file.read().strip()

        return int(raw_value) / 1000.0

    except Exception as error:
        print("[TEMP ERROR]", error)
        return None


def format_hourly_report(
    report_hour: datetime,
    temp_samples: list[float],
    motion_count: int,
) -> str:
    """
    Готовит текст отчета за прошедший час.
    """
    hour_start = report_hour.strftime("%H:00")
    hour_end = report_hour.strftime("%H:59")
    date_text = report_hour.strftime("%Y-%m-%d")

    if temp_samples:
        avg_temp = sum(temp_samples) / len(temp_samples)
        min_temp = min(temp_samples)
        max_temp = max(temp_samples)

        temp_text = (
            f"🌡 Средняя температура: {avg_temp:.1f} °C\n"
            f"❄️ Минимальная температура: {min_temp:.1f} °C\n"
            f"🔥 Максимальная температура: {max_temp:.1f} °C\n"
            f"🧪 Количество замеров: {len(temp_samples)}"
        )
    else:
        temp_text = "🌡 Температура: нет данных"

    return (
        "📊 Ежечасный отчет Raspberry Pi\n\n"
        f"📅 Дата: {date_text}\n"
        f"⏱ Период: {hour_start}–{hour_end}\n\n"
        f"{temp_text}\n\n"
        f"🎥 Сработок камеры за час: {motion_count}\n\n"
        "📷 Контрольный снимок камеры"
    )


def sender_worker(tdm_client: TDMClient) -> None:
    """
    Отдельный поток отправки сообщений в TDM.

    В очередь кладем пару:
    (image_path, message)
    """
    while not stop_event.is_set():
        try:
            task = send_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if task is None:
            send_queue.task_done()
            break

        image_path, message = task

        try:
            print(f"[SENDER] Sending image: {image_path}")
            tdm_client.send_image(image_path, message)
            print("[SENDER] Sent successfully")
        except Exception as error:
            print("[SENDER ERROR]", error)
        finally:
            send_queue.task_done()


def enqueue_photo(image_path, message: str) -> None:
    """
    Безопасно добавляет фото в очередь отправки.
    """
    if send_queue.full():
        print("[QUEUE] Send queue is full. Skipping message.")
        return

    send_queue.put((image_path, message))


def main() -> None:
    print("====================================")
    print(" Raspberry Camera Security Project")
    print("====================================")

    validate_config()
    ensure_project_dirs()

    tdm_client = TDMClient()
    camera = CameraMotionDetector()

    wifi_scanner = WiFiScannerReporter(
    tdm_client=tdm_client,
    interface=WIFI_INTERFACE,
    scan_interval_seconds=WIFI_SCAN_INTERVAL_SECONDS,
    )

    sender_thread = threading.Thread(
        target=sender_worker,
        args=(tdm_client,),
        daemon=True,
    )
    sender_thread.start()

    wifi_thread = threading.Thread(
    target=wifi_scanner.run_forever,
    args=(stop_event,),
    daemon=True,
    )
    wifi_thread.start()
    
    camera.open()

    servo_controller.open()

    last_motion_send_time = 0

    last_temp_sample_time = 0
    temp_sample_interval = 30

    current_report_hour = datetime.now().replace(
        minute=0,
        second=0,
        microsecond=0,
    )

    temp_samples: list[float] = []
    motion_count = 0

    try:
        print("[MAIN] Started. Press Ctrl+C to stop.")

        while True:
            frame = camera.read_frame()

            now_time = time.time()
            now_dt = datetime.now()
            now_hour = now_dt.replace(minute=0, second=0, microsecond=0)

            # 1. Собираем температуру раз в 30 секунд
            if now_time - last_temp_sample_time >= temp_sample_interval:
                cpu_temp = read_cpu_temp()

                if cpu_temp is not None:
                    temp_samples.append(cpu_temp)
                    print(f"[TEMP] CPU: {cpu_temp:.1f} °C")

                last_temp_sample_time = now_time

            # 2. Если начался новый час — отправляем отчет с фото
            if now_hour > current_report_hour:
                print("[REPORT] New hour. Sending hourly report with snapshot...")

                try:
                    snapshot_path = camera.save_snapshot_photo(frame.copy())

                    report_message = format_hourly_report(
                        report_hour=current_report_hour,
                        temp_samples=temp_samples,
                        motion_count=motion_count,
                    )

                    enqueue_photo(snapshot_path, report_message)

                except Exception as error:
                    print("[REPORT ERROR]", error)

                current_report_hour = now_hour
                temp_samples = []
                motion_count = 0

            # 3. Обычная логика движения
            motion_detected = camera.detect_motion(frame)

               if motion_detected:
                    motion_count += 1
                
                    if now_time - last_motion_send_time >= SEND_COOLDOWN_SECONDS:
                        print("[MOTION] Motion detected")
                
                        try:
                            # 1. Запускаем движение сервоприводов
                            # async = в отдельном потоке, чтобы не тормозить камеру
                            servo_controller.alert_motion_async()
                
                            # 2. Сохраняем фото
                            photo_path = camera.save_motion_photo(frame.copy())
                
                            # 3. Отправляем фото в TDM через очередь
                            enqueue_photo(photo_path, " Обнаружено движение. Робот активирован.")
                
                            last_motion_send_time = now_time
                
                        except Exception as error:
                            print("[MOTION ERROR]", error)

            time.sleep(0.03)

    except KeyboardInterrupt:
        print("\n[MAIN] Stopping by user...")

    finally:
        stop_event.set()
        send_queue.put(None)
        camera.close()
        servo_controller.close()
        print("[MAIN] Stopped")


if __name__ == "__main__":
    main()
