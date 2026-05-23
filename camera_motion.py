
import glob
import os
from datetime import datetime
from pathlib import Path

import cv2

from config import (
    PHOTO_DIR,
    MAX_PHOTOS,
    CAMERA_INDEX,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    JPEG_QUALITY,
    MOTION_AREA_THRESHOLD,
    MOTION_RESIZE_SCALE,
)


class CameraMotionDetector:
    """
    Отвечает только за камеру и обнаружение движения.
    Он не знает ничего про TDM, голос, моторы и статистику.
    """

    def __init__(self):
        self.cap = None
        self.previous_gray = None

    def open(self) -> None:
        print("[CAMERA] Opening camera...")

        self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

        if not self.cap.isOpened():
            raise RuntimeError("Камера не открылась")

        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("Не удалось получить первый кадр с камеры")

        self.previous_gray = self.prepare_motion_frame(frame)
        print("[CAMERA] Camera ready")

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            print("[CAMERA] Camera released")

    def read_frame(self):
        if self.cap is None:
            raise RuntimeError("Камера не открыта. Сначала вызови open().")

        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("Камера перестала отдавать кадры")

        return frame

    def prepare_motion_frame(self, frame):
        if MOTION_RESIZE_SCALE != 1.0:
            frame = cv2.resize(
                frame,
                None,
                fx=MOTION_RESIZE_SCALE,
                fy=MOTION_RESIZE_SCALE,
            )

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        return gray

    def detect_motion(self, frame) -> bool:
        current_gray = self.prepare_motion_frame(frame)

        diff = cv2.absdiff(self.previous_gray, current_gray)
        _, threshold = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        dilated = cv2.dilate(threshold, None, iterations=2)

        contours, _ = cv2.findContours(
            dilated,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        self.previous_gray = current_gray

        for contour in contours:
            area = cv2.contourArea(contour)
            if area >= MOTION_AREA_THRESHOLD:
                return True

        return False

    def save_motion_photo(self, frame) -> Path:
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        photo_path = PHOTO_DIR / f"motion_{timestamp}.jpg"

        ok = cv2.imwrite(
            str(photo_path),
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
        )

        if not ok:
            raise RuntimeError("Не удалось сохранить фото через cv2.imwrite")

        print(f"[PHOTO] Saved: {photo_path}")
        self.cleanup_old_photos()

        return photo_path

    def cleanup_old_photos(self) -> None:
        files = sorted(
            glob.glob(str(PHOTO_DIR / "*.jpg")),
            key=os.path.getmtime,
        )

        if len(files) <= MAX_PHOTOS:
            return

        files_to_delete = files[: len(files) - MAX_PHOTOS]

        for file_path in files_to_delete:
            try:
                os.remove(file_path)
                print(f"[CLEANUP] Deleted old photo: {file_path}")
            except Exception as error:
                print(f"[CLEANUP ERROR] {file_path}: {error}")
