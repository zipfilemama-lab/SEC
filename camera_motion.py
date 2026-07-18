import glob
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2

from config import (
    PHOTO_DIR,
    MAX_PHOTOS,
    JPEG_QUALITY,
    RPI_CAMERA_BRIGHTNESS,
    RPI_CAMERA_CONTRAST,
    RPI_CAMERA_SHARPNESS,
    RPI_CAMERA_EXPOSURE_TIME_US,
    RPI_CAMERA_DIGITAL_ZOOM,
)


class CameraMotionDetector:
    """
    Универсальный класс камеры.

    Поддерживает два режима:

    backend="picamera2"
        Камера Raspberry Pi в CSI-разъёме.

    backend="v4l2"
        Обычная USB-камера через /dev/video...
    """

    def __init__(
        self,
        name: str,
        backend: str,
        device: str | int | None,
        width: int,
        height: int,
        fps: int,
        motion_area_threshold: int,
        motion_resize_scale: float,
    ):
        self.name = name
        self.backend = backend
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.motion_area_threshold = motion_area_threshold
        self.motion_resize_scale = motion_resize_scale

        self.cap = None
        self.picam2 = None
        self.previous_gray = None
        self.last_motion_boxes: list[
            tuple[int, int, int, int]
        ] = []

        self.safe_name = self._make_safe_name(name)

    @staticmethod
    def _make_safe_name(value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"[^a-z0-9_-]+", "_", value)
        return value.strip("_") or "camera"

    def open(self) -> None:
        print(
            f"[CAMERA {self.name}] Opening "
            f"backend={self.backend}, device={self.device}"
        )

        if self.backend == "picamera2":
            self._open_picamera2()
        elif self.backend == "v4l2":
            self._open_v4l2()
        else:
            raise ValueError(
                f"Неизвестный тип камеры: {self.backend}"
            )

        first_frame = self.read_frame()
        self.previous_gray = self.prepare_motion_frame(
            first_frame
        )

        print(
            f"[CAMERA {self.name}] Ready: "
            f"{first_frame.shape[1]}x{first_frame.shape[0]}"
        )

    def _open_picamera2(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError as error:
            raise RuntimeError(
                "Не установлена библиотека Picamera2.\n"
                "Выполни: sudo apt install python3-picamera2"
            ) from error

        self.picam2 = Picamera2()

        configuration = self.picam2.create_video_configuration(
            main={
                "size": (self.width, self.height),
                "format": "RGB888",
            },
            controls={
                "FrameRate": float(self.fps),
            },
            buffer_count=4,
        )

        self.picam2.configure(configuration)
        self.picam2.start(show_preview=False)

        # Камере требуется время для запуска автоэкспозиции
        # и автофокуса.
        time.sleep(2)

        self._apply_picamera2_image_controls()

        # После применения настроек даём камере несколько кадров,
        # чтобы изменения успели вступить в силу.
        time.sleep(1)

        self._print_picamera2_settings()

    def _apply_picamera2_image_controls(self) -> None:
        """
        Применяет настройки Raspberry Pi Camera из файла .env.

        Настройки применяются только к CSI-камере Picamera2.
        USB-камера этими параметрами не изменяется.
        """
        if self.picam2 is None:
            raise RuntimeError(
                f"Камера {self.name} не открыта"
            )

        available_controls = self.picam2.camera_controls

        controls_to_apply: dict[str, Any] = {
            "Brightness": float(RPI_CAMERA_BRIGHTNESS),
            "Contrast": float(RPI_CAMERA_CONTRAST),
            "Sharpness": float(RPI_CAMERA_SHARPNESS),
        }

        # Значение 0 означает автоматическую выдержку.
        if RPI_CAMERA_EXPOSURE_TIME_US > 0:
            exposure_time = int(
                RPI_CAMERA_EXPOSURE_TIME_US
            )

            # На старых версиях libcamera/Picamera2
            # ручная выдержка включается через AeEnable=False.
            if "AeEnable" in available_controls:
                controls_to_apply["AeEnable"] = False

            # На некоторых новых версиях libcamera доступны
            # отдельные режимы ручной выдержки и усиления.
            if "ExposureTimeMode" in available_controls:
                controls_to_apply["ExposureTimeMode"] = 1

            controls_to_apply["ExposureTime"] = exposure_time

        self._validate_picamera2_controls(
            controls_to_apply
        )

        self.picam2.set_controls(controls_to_apply)

        if RPI_CAMERA_DIGITAL_ZOOM > 1.0:
            self._apply_digital_zoom(
                float(RPI_CAMERA_DIGITAL_ZOOM)
            )

    def _validate_picamera2_controls(
        self,
        requested_controls: dict[str, Any],
    ) -> None:
        """
        Проверяет, что используемая камера и текущая версия
        Picamera2 поддерживают запрошенные параметры.
        """
        if self.picam2 is None:
            return

        available_controls = self.picam2.camera_controls

        unsupported = [
            name
            for name in requested_controls
            if name not in available_controls
        ]

        if unsupported:
            raise RuntimeError(
                "Камера или текущая версия Picamera2 "
                "не поддерживает параметры: "
                + ", ".join(unsupported)
            )

        for name, value in requested_controls.items():
            limits = available_controls.get(name)

            if not limits or len(limits) < 2:
                continue

            minimum = limits[0]
            maximum = limits[1]

            # Сложные параметры вроде ScalerCrop здесь
            # не проверяются.
            if not isinstance(value, (int, float)):
                continue

            if not isinstance(minimum, (int, float)):
                continue

            if not isinstance(maximum, (int, float)):
                continue

            if value < minimum or value > maximum:
                raise RuntimeError(
                    f"{name}={value} выходит за диапазон "
                    f"камеры: {minimum}..{maximum}"
                )

    def _apply_digital_zoom(
        self,
        zoom_factor: float,
    ) -> None:
        """
        Выполняет цифровой зум через ScalerCrop.

        Зум центрирован относительно середины изображения.
        При zoom_factor=1.0 кадр не обрезается.
        """
        if self.picam2 is None:
            return

        if "ScalerCrop" not in self.picam2.camera_controls:
            raise RuntimeError(
                "Эта камера не поддерживает ScalerCrop"
            )

        metadata = self.picam2.capture_metadata()
        current_crop = metadata.get("ScalerCrop")

        if (
            current_crop is None
            or len(current_crop) != 4
        ):
            raise RuntimeError(
                "Не удалось получить исходную область "
                "ScalerCrop для цифрового зума"
            )

        crop_x, crop_y, crop_width, crop_height = (
            int(current_crop[0]),
            int(current_crop[1]),
            int(current_crop[2]),
            int(current_crop[3]),
        )

        new_width = max(
            2,
            int(crop_width / zoom_factor),
        )
        new_height = max(
            2,
            int(crop_height / zoom_factor),
        )

        # Для совместимости с сенсорами, где размеры должны
        # быть чётными.
        new_width -= new_width % 2
        new_height -= new_height % 2

        new_x = crop_x + (crop_width - new_width) // 2
        new_y = crop_y + (crop_height - new_height) // 2

        new_x -= new_x % 2
        new_y -= new_y % 2

        crop = (
            new_x,
            new_y,
            new_width,
            new_height,
        )

        self.picam2.set_controls(
            {
                "ScalerCrop": crop,
            }
        )

        print(
            f"[CAMERA {self.name}] Digital zoom: "
            f"{zoom_factor:.2f}x, crop={crop}"
        )

    def _print_picamera2_settings(self) -> None:
        """
        Выводит запрошенные настройки и часть фактических
        метаданных камеры после запуска.
        """
        if self.picam2 is None:
            return

        metadata = self.picam2.capture_metadata()

        print(
            f"[CAMERA {self.name}] Image settings:"
        )
        print(
            f"  Brightness: "
            f"{RPI_CAMERA_BRIGHTNESS}"
        )
        print(
            f"  Contrast: "
            f"{RPI_CAMERA_CONTRAST}"
        )
        print(
            f"  Sharpness: "
            f"{RPI_CAMERA_SHARPNESS}"
        )

        if RPI_CAMERA_EXPOSURE_TIME_US > 0:
            print(
                "  Exposure mode: manual"
            )
            print(
                "  Requested exposure: "
                f"{RPI_CAMERA_EXPOSURE_TIME_US} us"
            )
        else:
            print(
                "  Exposure mode: automatic"
            )

        print(
            "  Actual exposure: "
            f"{metadata.get('ExposureTime', 'unknown')} us"
        )
        print(
            "  Digital zoom: "
            f"{RPI_CAMERA_DIGITAL_ZOOM}x"
        )
        print(
            "  ScalerCrop: "
            f"{metadata.get('ScalerCrop', 'unknown')}"
        )

    def _open_v4l2(self) -> None:
        source: Any = self.device

        if isinstance(source, str) and source.isdigit():
            source = int(source)

        self.cap = cv2.VideoCapture(
            source,
            cv2.CAP_V4L2,
        )

        self.cap.set(
            cv2.CAP_PROP_BUFFERSIZE,
            1,
        )

        self.cap.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            self.width,
        )

        self.cap.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            self.height,
        )

        self.cap.set(
            cv2.CAP_PROP_FPS,
            self.fps,
        )

        # Для многих USB-камер MJPG позволяет получить
        # 1280x720 без слишком большой нагрузки на USB.
        self.cap.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc(*"MJPG"),
        )

        if not self.cap.isOpened():
            raise RuntimeError(
                f"USB-камера не открылась: {self.device}"
            )

        # Несколько первых кадров могут быть пустыми.
        for _ in range(10):
            ok, frame = self.cap.read()

            if ok and frame is not None:
                return

            time.sleep(0.1)

        raise RuntimeError(
            f"USB-камера {self.device} не отдаёт кадры"
        )

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        if self.picam2 is not None:
            try:
                self.picam2.stop()
            finally:
                self.picam2.close()

            self.picam2 = None

        print(f"[CAMERA {self.name}] Released")

    def read_frame(self):
        if self.backend == "picamera2":
            if self.picam2 is None:
                raise RuntimeError(
                    f"Камера {self.name} не открыта"
                )

            frame = self.picam2.capture_array("main")

            if frame is None:
                raise RuntimeError(
                    f"Камера {self.name} вернула пустой кадр"
                )

            if (
                len(frame.shape) == 3
                and frame.shape[2] == 4
            ):
                frame = cv2.cvtColor(
                    frame,
                    cv2.COLOR_BGRA2BGR,
                )

            return frame

        if self.backend == "v4l2":
            if self.cap is None:
                raise RuntimeError(
                    f"Камера {self.name} не открыта"
                )

            ok, frame = self.cap.read()

            if not ok or frame is None:
                raise RuntimeError(
                    f"Камера {self.name} "
                    "перестала отдавать кадры"
                )

            return frame

        raise RuntimeError(
            f"Неизвестный backend камеры: {self.backend}"
        )

    def prepare_motion_frame(self, frame):
        """
        Подготавливает кадр для поиска движения:

        1. Уменьшает изображение.
        2. Переводит его в серый цвет.
        3. Размывает мелкий цифровой шум.
        """
        prepared = frame

        if self.motion_resize_scale != 1.0:
            prepared = cv2.resize(
                prepared,
                None,
                fx=self.motion_resize_scale,
                fy=self.motion_resize_scale,
            )

        gray = cv2.cvtColor(
            prepared,
            cv2.COLOR_BGR2GRAY,
        )

        gray = cv2.GaussianBlur(
            gray,
            (5, 5),
            0,
        )

        return gray

    def detect_motion(self, frame) -> bool:
        current_gray = self.prepare_motion_frame(frame)

        if self.previous_gray is None:
            self.previous_gray = current_gray
            return False

        difference = cv2.absdiff(
            self.previous_gray,
            current_gray,
        )

        _, threshold = cv2.threshold(
            difference,
            25,
            255,
            cv2.THRESH_BINARY,
        )

        dilated = cv2.dilate(
            threshold,
            None,
            iterations=2,
        )

        contours, _ = cv2.findContours(
            dilated,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        self.previous_gray = current_gray
        self.last_motion_boxes = []

        motion_detected = False

        for contour in contours:
            area = cv2.contourArea(contour)

            if area < self.motion_area_threshold:
                continue

            x, y, width, height = cv2.boundingRect(
                contour
            )

            if self.motion_resize_scale != 1.0:
                scale = 1.0 / self.motion_resize_scale

                x = int(x * scale)
                y = int(y * scale)
                width = int(width * scale)
                height = int(height * scale)

            self.last_motion_boxes.append(
                (x, y, width, height)
            )

            motion_detected = True

        return motion_detected

    def draw_motion_boxes(self, frame):
        annotated = frame.copy()

        for x, y, width, height in self.last_motion_boxes:
            cv2.rectangle(
                annotated,
                (x, y),
                (x + width, y + height),
                (0, 255, 0),
                3,
            )

            cv2.putText(
                annotated,
                "MOTION",
                (x, max(y - 10, 25)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

        self._draw_camera_label(
            annotated,
            event_text="MOTION",
        )

        return annotated

    def _draw_camera_label(
        self,
        frame,
        event_text: str,
    ) -> None:
        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        top_text = f"{self.name} | {event_text}"

        cv2.putText(
            frame,
            top_text,
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.85,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            frame,
            timestamp,
            (20, frame.shape[0] - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    def save_motion_photo(self, frame) -> Path:
        PHOTO_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        annotated = self.draw_motion_boxes(frame)

        timestamp = datetime.now().strftime(
            "%Y-%m-%d_%H-%M-%S_%f"
        )

        photo_path = (
            PHOTO_DIR
            / f"{self.safe_name}_motion_{timestamp}.jpg"
        )

        self._write_jpeg(
            photo_path,
            annotated,
        )

        print(
            f"[PHOTO {self.name}] Saved: {photo_path}"
        )

        self.cleanup_old_photos()

        return photo_path

    def save_snapshot_photo(self, frame) -> Path:
        PHOTO_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        snapshot = frame.copy()

        self._draw_camera_label(
            snapshot,
            event_text="SNAPSHOT",
        )

        timestamp = datetime.now().strftime(
            "%Y-%m-%d_%H-%M-%S_%f"
        )

        photo_path = (
            PHOTO_DIR
            / f"{self.safe_name}_snapshot_{timestamp}.jpg"
        )

        self._write_jpeg(
            photo_path,
            snapshot,
        )

        print(
            f"[SNAPSHOT {self.name}] Saved: {photo_path}"
        )

        self.cleanup_old_photos()

        return photo_path

    @staticmethod
    def _write_jpeg(
        photo_path: Path,
        frame,
    ) -> None:
        ok = cv2.imwrite(
            str(photo_path),
            frame,
            [
                int(cv2.IMWRITE_JPEG_QUALITY),
                JPEG_QUALITY,
            ],
        )

        if not ok:
            raise RuntimeError(
                f"Не удалось сохранить фотографию: "
                f"{photo_path}"
            )

    def cleanup_old_photos(self) -> None:
        files = sorted(
            glob.glob(str(PHOTO_DIR / "*.jpg")),
            key=os.path.getmtime,
        )

        if len(files) <= MAX_PHOTOS:
            return

        files_to_delete = files[
            : len(files) - MAX_PHOTOS
        ]

        for file_path in files_to_delete:
            try:
                os.remove(file_path)

                print(
                    f"[CLEANUP] Deleted: {file_path}"
                )
            except Exception as error:
                print(
                    f"[CLEANUP ERROR] "
                    f"{file_path}: {error}"
                )
