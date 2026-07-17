import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from config import (
    ensure_project_dirs,
    validate_config,
    SEND_COOLDOWN_SECONDS,
    MOTION_CONFIRM_FRAMES,
    WIFI_INTERFACE,
    WIFI_SCAN_INTERVAL_SECONDS,
    RPI_CAMERA_ENABLED,
    RPI_CAMERA_WIDTH,
    RPI_CAMERA_HEIGHT,
    RPI_CAMERA_FPS,
    RPI_MOTION_AREA_THRESHOLD,
    RPI_MOTION_RESIZE_SCALE,
    USB_CAMERA_ENABLED,
    USB_CAMERA_DEVICE,
    USB_CAMERA_WIDTH,
    USB_CAMERA_HEIGHT,
    USB_CAMERA_FPS,
    USB_MOTION_AREA_THRESHOLD,
    USB_MOTION_RESIZE_SCALE,
    SERVO_ENABLED,
)

from camera_motion import CameraMotionDetector
from tdm_client import TDMClient
from wifi_scanner import WiFiScannerReporter


send_queue: queue.Queue = queue.Queue(maxsize=10)
stop_event = threading.Event()

latest_frames_lock = threading.Lock()
latest_frames: dict[str, object] = {}

motion_stats_lock = threading.Lock()
motion_stats: dict[str, int] = {}


def set_latest_frame(
    camera_key: str,
    frame,
) -> None:
    if frame is None:
        return

    with latest_frames_lock:
        latest_frames[camera_key] = frame.copy()


def get_latest_frame_copy(camera_key: str):
    with latest_frames_lock:
        frame = latest_frames.get(camera_key)

        if frame is None:
            return None

        return frame.copy()


def increment_motion_count(camera_key: str) -> None:
    with motion_stats_lock:
        motion_stats[camera_key] = (
            motion_stats.get(camera_key, 0) + 1
        )


def take_and_reset_motion_stats() -> dict[str, int]:
    with motion_stats_lock:
        result = dict(motion_stats)

        for key in motion_stats:
            motion_stats[key] = 0

        return result


def read_cpu_temp() -> float | None:
    try:
        with open(
            "/sys/class/thermal/thermal_zone0/temp",
            "r",
            encoding="utf-8",
        ) as file:
            raw_value = file.read().strip()

        return int(raw_value) / 1000.0

    except Exception as error:
        print("[TEMP ERROR]", error)
        return None


def format_hourly_report(
    report_hour: datetime,
    temp_samples: list[float],
    camera_counts: dict[str, int],
) -> str:
    hour_start = report_hour.strftime("%H:00")
    hour_end = report_hour.strftime("%H:59")
    date_text = report_hour.strftime("%Y-%m-%d")

    if temp_samples:
        average_temp = sum(temp_samples) / len(temp_samples)
        minimum_temp = min(temp_samples)
        maximum_temp = max(temp_samples)

        temperature_text = (
            f"Средняя температура: {average_temp:.1f} °C\n"
            f"Минимальная температура: {minimum_temp:.1f} °C\n"
            f"Максимальная температура: {maximum_temp:.1f} °C\n"
            f"Количество замеров: {len(temp_samples)}"
        )
    else:
        temperature_text = "Температура: нет данных"

    rpi_count = camera_counts.get(
        "raspberry",
        0,
    )

    usb_count = camera_counts.get(
        "usb",
        0,
    )

    return (
        "Ежечасный отчет Raspberry Pi\n\n"
        f"Дата: {date_text}\n"
        f"Период: {hour_start}–{hour_end}\n\n"
        f"{temperature_text}\n\n"
        "Сработки камер:\n"
        f"• Raspberry Pi Camera: {rpi_count}\n"
        f"• USB Camera: {usb_count}"
    )


def sender_worker(tdm_client: TDMClient) -> None:
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
            print(
                f"[SENDER] Sending image: {image_path}"
            )

            success = tdm_client.send_image(
                image_path,
                message,
            )

            if success:
                print("[SENDER] Sent successfully")
            else:
                print("[SENDER] TDM returned failure")

        except Exception as error:
            print("[SENDER ERROR]", error)

        finally:
            send_queue.task_done()


def enqueue_photo(
    image_path: str | Path,
    message: str,
) -> None:
    if send_queue.full():
        print(
            "[QUEUE] Send queue is full. "
            "Skipping photograph."
        )
        return

    send_queue.put(
        (
            Path(image_path),
            message,
        )
    )


def camera_worker(
    camera_key: str,
    camera: CameraMotionDetector,
) -> None:
    """
    Отдельный поток одной камеры.

    У каждой камеры:
    - собственный поиск движения;
    - собственный счётчик подтверждающих кадров;
    - собственный таймер отправки.
    """
    last_send_time = 0.0
    confirm_frames = 0

    print(
        f"[WORKER {camera.name}] Started"
    )

    while not stop_event.is_set():
        try:
            frame = camera.read_frame()
            set_latest_frame(
                camera_key,
                frame,
            )

            motion_detected = camera.detect_motion(
                frame,
            )

            if motion_detected:
                confirm_frames += 1
            else:
                confirm_frames = 0

            if confirm_frames >= MOTION_CONFIRM_FRAMES:
                current_time = time.time()

                if (
                    current_time - last_send_time
                    >= SEND_COOLDOWN_SECONDS
                ):
                    print(
                        f"[MOTION {camera.name}] "
                        "Confirmed motion detected"
                    )

                    photo_path = camera.save_motion_photo(
                        frame.copy()
                    )

                    enqueue_photo(
                        photo_path,
                        (
                            "Обнаружено движение.\n"
                            f"Камера: {camera.name}"
                        ),
                    )

                    increment_motion_count(
                        camera_key
                    )

                    last_send_time = current_time

                confirm_frames = 0

            time.sleep(0.02)

        except Exception as error:
            print(
                f"[CAMERA WORKER ERROR {camera.name}]",
                error,
            )

            # Не завершаем весь проект из-за одного
            # временного сбоя чтения камеры.
            time.sleep(1)

    print(
        f"[WORKER {camera.name}] Stopped"
    )


def create_cameras() -> list[
    tuple[str, CameraMotionDetector]
]:
    cameras: list[
        tuple[str, CameraMotionDetector]
    ] = []

    if RPI_CAMERA_ENABLED:
        raspberry_camera = CameraMotionDetector(
            name="Raspberry Pi Camera",
            backend="picamera2",
            device=None,
            width=RPI_CAMERA_WIDTH,
            height=RPI_CAMERA_HEIGHT,
            fps=RPI_CAMERA_FPS,
            motion_area_threshold=(
                RPI_MOTION_AREA_THRESHOLD
            ),
            motion_resize_scale=(
                RPI_MOTION_RESIZE_SCALE
            ),
        )

        cameras.append(
            (
                "raspberry",
                raspberry_camera,
            )
        )

    if USB_CAMERA_ENABLED:
        usb_camera = CameraMotionDetector(
            name="USB Camera",
            backend="v4l2",
            device=USB_CAMERA_DEVICE,
            width=USB_CAMERA_WIDTH,
            height=USB_CAMERA_HEIGHT,
            fps=USB_CAMERA_FPS,
            motion_area_threshold=(
                USB_MOTION_AREA_THRESHOLD
            ),
            motion_resize_scale=(
                USB_MOTION_RESIZE_SCALE
            ),
        )

        cameras.append(
            (
                "usb",
                usb_camera,
            )
        )

    return cameras


def main() -> None:
    print("====================================")
    print(" Dual Camera Security Project")
    print("====================================")

    validate_config()
    ensure_project_dirs()

    tdm_client = TDMClient()
    configured_cameras = create_cameras()

    active_cameras: list[
        tuple[str, CameraMotionDetector]
    ] = []

    for camera_key, camera in configured_cameras:
        try:
            camera.open()

            active_cameras.append(
                (
                    camera_key,
                    camera,
                )
            )

            motion_stats[camera_key] = 0

        except Exception as error:
            print(
                f"[CAMERA START ERROR {camera.name}]",
                error,
            )

    if not active_cameras:
        raise RuntimeError(
            "Не удалось открыть ни одну камеру"
        )

    print(
        "[MAIN] Active cameras:",
        ", ".join(
            camera.name
            for _, camera in active_cameras
        ),
    )

    sender_thread = threading.Thread(
        target=sender_worker,
        args=(tdm_client,),
        daemon=True,
        name="tdm-sender",
    )

    sender_thread.start()

    wifi_scanner = WiFiScannerReporter(
        tdm_client=tdm_client,
        interface=WIFI_INTERFACE,
        scan_interval_seconds=(
            WIFI_SCAN_INTERVAL_SECONDS
        ),
    )

    wifi_thread = threading.Thread(
        target=wifi_scanner.run_forever,
        args=(stop_event,),
        daemon=True,
        name="wifi-scanner",
    )

    wifi_thread.start()

    camera_threads: list[threading.Thread] = []

    for camera_key, camera in active_cameras:
        thread = threading.Thread(
            target=camera_worker,
            args=(
                camera_key,
                camera,
            ),
            daemon=True,
            name=f"camera-{camera_key}",
        )

        thread.start()
        camera_threads.append(thread)

    if SERVO_ENABLED:
        print(
            "[SERVO] SERVO_ENABLED=true, "
            "но управление сервоприводами в режиме "
            "двух камер пока не запускается."
        )
    else:
        print("[SERVO] Disabled")

    last_temp_sample_time = 0.0
    temp_sample_interval = 30

    current_report_hour = datetime.now().replace(
        minute=0,
        second=0,
        microsecond=0,
    )

    temp_samples: list[float] = []

    try:
        print(
            "[MAIN] Started. Press Ctrl+C to stop."
        )

        while not stop_event.is_set():
            current_time = time.time()
            current_datetime = datetime.now()

            current_hour = current_datetime.replace(
                minute=0,
                second=0,
                microsecond=0,
            )

            if (
                current_time - last_temp_sample_time
                >= temp_sample_interval
            ):
                cpu_temperature = read_cpu_temp()

                if cpu_temperature is not None:
                    temp_samples.append(
                        cpu_temperature
                    )

                    print(
                        f"[TEMP] CPU: "
                        f"{cpu_temperature:.1f} °C"
                    )

                last_temp_sample_time = current_time

            if current_hour > current_report_hour:
                print(
                    "[REPORT] Sending hourly report"
                )

                camera_counts = (
                    take_and_reset_motion_stats()
                )

                report_message = format_hourly_report(
                    report_hour=current_report_hour,
                    temp_samples=temp_samples,
                    camera_counts=camera_counts,
                )

                for camera_key, camera in active_cameras:
                    frame = get_latest_frame_copy(
                        camera_key
                    )

                    if frame is None:
                        print(
                            f"[REPORT {camera.name}] "
                            "No frame available"
                        )
                        continue

                    try:
                        snapshot_path = (
                            camera.save_snapshot_photo(
                                frame
                            )
                        )

                        enqueue_photo(
                            snapshot_path,
                            (
                                f"{report_message}\n\n"
                                f"Контрольный снимок: "
                                f"{camera.name}"
                            ),
                        )

                    except Exception as error:
                        print(
                            f"[REPORT ERROR "
                            f"{camera.name}]",
                            error,
                        )

                current_report_hour = current_hour
                temp_samples = []

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[MAIN] Stopping by user...")

    finally:
        stop_event.set()

        try:
            send_queue.put_nowait(None)
        except queue.Full:
            print(
                "[QUEUE] Cannot add stop marker: "
                "queue is full"
            )

        for _, camera in active_cameras:
            try:
                camera.close()
            except Exception as error:
                print(
                    f"[CAMERA CLOSE ERROR "
                    f"{camera.name}]",
                    error,
                )

        print("[MAIN] Stopped")


if __name__ == "__main__":
    main()
PY
