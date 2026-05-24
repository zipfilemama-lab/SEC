import queue
import threading
import time
from datetime import datetime

from config import (
    ensure_project_dirs,
    validate_config,
    SEND_COOLDOWN_SECONDS,
)
from camera_motion import CameraMotionDetector
from tdm_client import TDMClient


send_queue = queue.Queue(maxsize=3)
stop_event = threading.Event()


def read_cpu_temp() -> float | None:
    """
    Читает температуру CPU Raspberry Pi.

    Raspberry хранит температуру тут:
    /sys/class/thermal/thermal_zone0/temp

    Там число в тысячных долях градуса.
    Например:
    48000 = 48.0 °C
    """
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as file:
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
    Готовит текст статистики за час.
    """
    if temp_samples:
        avg_temp = sum(temp_samples) / len(temp_samples)
        max_temp = max(temp_samples)

        temp_text = (
            f"Средняя температура: {avg_temp:.1f} °C\n"
            f"Максимальная температура: {max_temp:.1f} °C"
        )
    else:
        temp_text = "Температура: нет данных"

    return (
        "📊 Ежечасный отчет Raspberry Pi\n\n"
        f"Период: {report_hour.strftime('%Y-%m-%d %H:00')} - "
        f"{(report_hour.replace(minute=0, second=0, microsecond=0)).strftime('%H')}:59\n\n"
        f"{temp_text}\n"
        f"Сработок камеры за час: {motion_count}\n\n"
        "📷 Контрольный снимок камеры"
    )


def sender_worker(tdm_client: TDMClient) -> None:
    """
    Отдельный поток для отправки фото в TDM.

    В очередь теперь кладем не просто путь к фото,
    а пару:
    (image_path, message)

    Так можно отправлять разные сообщения:
    - при движении: 'Обнаружено движение'
    - раз в час: статистика температуры
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
            tdm_client.send_image(image_path, message)
        finally:
            send_queue.task_done()


def enqueue_photo(image_path, message: str) -> None:
    """
    Безопасно добавляет фото в очередь отправки.
    """
    if not send_queue.full():
        send_queue.put((image_path, message))
    else:
        print("[QUEUE] Send queue is full. Skipping message.")


def main() -> None:
    print("====================================")
    print(" Raspberry Camera Security Project")
    print("====================================")

    validate_config()
    ensure_project_dirs()

    tdm_client = TDMClient()
    camera = CameraMotionDetector()

    sender_thread = threading.Thread(
        target=sender_worker,
        args=(tdm_client,),
        daemon=True,
    )
    sender_thread.start()

    camera.open()

    last_send_time = 0

    # Температуру будем читать не каждый кадр, а раз в 30 секунд.
    last_temp_sample_time = 0
    temp_sample_interval = 30

    # Текущий час, за который собираем статистику.
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
            motion_detected = camera.detect_motion(frame)

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

            # 2. Если начался новый час — отправляем отчет за прошлый час
            if now_hour > current_report_hour:
                print("[REPORT] New hour. Sending hourly report...")

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

                # Сбрасываем статистику и начинаем новый час
                current_report_hour = now_hour
                temp_samples = []
                motion_count = 0

            # 3. Обычная логика движения
            if motion_detected:
                motion_count += 1

                if now_time - last_send_time >= SEND_COOLDOWN_SECONDS:
                    print("[MOTION] Motion detected")

                    try:
                        photo_path = camera.save_motion_photo(frame.copy())
                        enqueue_photo(photo_path, "🚨 Обнаружено движение")
                        last_send_time = now_time

                    except Exception as error:
                        print("[MOTION ERROR]", error)

            time.sleep(0.03)

    except KeyboardInterrupt:
        print("\n[MAIN] Stopping by user...")

    finally:
        stop_event.set()
        send_queue.put(None)
        camera.close()
        print("[MAIN] Stopped")


if __name__ == "__main__":
    main()
