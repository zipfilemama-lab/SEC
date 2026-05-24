import queue
import threading
import time

from config import (
    ensure_project_dirs,
    validate_config,
    SEND_COOLDOWN_SECONDS,
    WIFI_INTERFACE,
    WIFI_SCAN_INTERVAL_SECONDS,
)
from camera_motion import CameraMotionDetector
from tdm_client import TDMClient
from cpu_stats import CPUStatsReporter
from wifi_scanner import WiFiScannerReporter


send_queue = queue.Queue(maxsize=1)
stop_event = threading.Event()


def sender_worker(tdm_client: TDMClient) -> None:
    """
    Отдельный поток для отправки фото в TDM.

    Зачем он нужен:
    - камера продолжает работать;
    - отправка фото не блокирует обнаружение движения;
    - очередь maxsize=1 защищает от завала сообщений.
    """

    while not stop_event.is_set():
        try:
            image_path = send_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if image_path is None:
            send_queue.task_done()
            break

        try:
            tdm_client.send_image(image_path, "🚨 Обнаружено движение")
        finally:
            send_queue.task_done()


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

    cpu_stats_reporter = CPUStatsReporter(
        tdm_client=tdm_client,
        sample_interval_seconds=60,
    )

    cpu_stats_thread = threading.Thread(
        target=cpu_stats_reporter.run_forever,
        args=(stop_event,),
        daemon=True,
    )
    cpu_stats_thread.start()

    wifi_scanner_reporter = WiFiScannerReporter(
        tdm_client=tdm_client,
        interface=WIFI_INTERFACE,
        scan_interval_seconds=WIFI_SCAN_INTERVAL_SECONDS,
    )

    wifi_scanner_thread = threading.Thread(
        target=wifi_scanner_reporter.run_forever,
        args=(stop_event,),
        daemon=True,
    )
    wifi_scanner_thread.start()

    camera.open()

    last_send_time = 0

    try:
        print("[MAIN] Started. Press Ctrl+C to stop.")

        while True:
            frame = camera.read_frame()
            motion_detected = camera.detect_motion(frame)

            now = time.time()

            if motion_detected and now - last_send_time >= SEND_COOLDOWN_SECONDS:
                print("[MOTION] Motion detected")

                try:
                    photo_path = camera.save_motion_photo(frame.copy())

                    if not send_queue.full():
                        send_queue.put(photo_path)
                        last_send_time = now
                    else:
                        print("[QUEUE] Send queue is full. Skipping this event.")

                except Exception as error:
                    print("[MOTION ERROR]", error)

            time.sleep(0.03)

    except KeyboardInterrupt:
        print("\n[MAIN] Stopping by user...")

    finally:
        stop_event.set()

        try:
            send_queue.put_nowait(None)
        except queue.Full:
            pass

        camera.close()
        print("[MAIN] Stopped")


if __name__ == "__main__":
    main()
