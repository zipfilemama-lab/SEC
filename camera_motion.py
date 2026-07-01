import glob
import os
from datetime import datetime
from pathlib import Path

import cv2

from PIL import Image, ImageDraw, ImageFont

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
    Отвечает за камеру, поиск движения и сохранение фотографий.
    """

    def __init__(self):
        self.cap = None
        self.previous_gray = None
        self.last_motion_boxes = []

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
        """
        Готовим кадр для поиска движения:
        - уменьшаем картинку;
        - переводим в серый цвет;
        - размываем шум.
        """
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
        """
        Возвращает True, если движение найдено.
        Также сохраняет координаты рамок в self.last_motion_boxes.
        """
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
        self.last_motion_boxes = []

        motion_detected = False

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < MOTION_AREA_THRESHOLD:
                continue

            x, y, w, h = cv2.boundingRect(contour)

            if MOTION_RESIZE_SCALE != 1.0:
                scale = 1.0 / MOTION_RESIZE_SCALE
                x = int(x * scale)
                y = int(y * scale)
                w = int(w * scale)
                h = int(h * scale)

            self.last_motion_boxes.append((x, y, w, h))
            motion_detected = True

        return motion_detected

    def draw_motion_boxes(self, frame):
        """
        Рисует рамки на местах движения.
        """
        annotated = frame.copy()

        for x, y, w, h in self.last_motion_boxes:
            cv2.rectangle(
                annotated,
                (x, y),
                (x + w, y + h),
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

        timestamp_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(
            annotated,
            timestamp_text,
            (20, annotated.shape[0] - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        return annotated

    def get_font(self, size=32):
        """
        Ищем шрифт, который умеет кириллицу.
        На Kali/Raspberry чаще всего есть DejaVuSans.
        """
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                return ImageFont.truetype(font_path, size)

        print("[FONT WARNING] Не найден TTF-шрифт. Кириллица может не отображаться.")
        return ImageFont.load_default()

    def draw_russian_text_block(self, frame, lines):
        """
        Рисует русский текст на кадре через Pillow.
        OpenCV cv2.putText кириллицу обычно превращает в ?????.
        """
        # OpenCV хранит кадр как BGR, Pillow работает с RGB.
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb_frame)

        draw = ImageDraw.Draw(image)

        title_font = self.get_font(34)
        text_font = self.get_font(26)

        x = 20
        y = 20
        padding = 14
        line_gap = 10

        # Считаем размер фона под текст.
        text_boxes = []

        for index, line in enumerate(lines):
            font = title_font if index == 0 else text_font
            bbox = draw.textbbox((0, 0), line, font=font)
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            text_boxes.append((width, height, font))

        block_width = max(width for width, height, font in text_boxes) + padding * 2
        block_height = (
            sum(height for width, height, font in text_boxes)
            + line_gap * (len(lines) - 1)
            + padding * 2
        )

        # Полупрозрачный тёмный фон.
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        overlay_draw.rectangle(
            (x, y, x + block_width, y + block_height),
            fill=(0, 0, 0, 150),
        )

        image = Image.alpha_composite(image.convert("RGBA"), overlay)

        draw = ImageDraw.Draw(image)

        current_y = y + padding

        for index, line in enumerate(lines):
            font = title_font if index == 0 else text_font

            # Белый текст.
            draw.text(
                (x + padding, current_y),
                line,
                font=font,
                fill=(255, 255, 255, 255),
            )

            bbox = draw.textbbox((0, 0), line, font=font)
            line_height = bbox[3] - bbox[1]
            current_y += line_height + line_gap

        # Возвращаем обратно в формат OpenCV BGR.
        final_rgb = image.convert("RGB")
        final_frame = cv2.cvtColor(
            src=cv2.UMat(cv2.cvtColor(
                cv2.cvtColor(
                    __import__("numpy").array(final_rgb),
                    cv2.COLOR_RGB2BGR
                ),
                cv2.COLOR_BGR2RGB
            )).get(),
            code=cv2.COLOR_RGB2BGR,
        )

        # Более простой и надежный возврат через numpy:
        import numpy as np
        return cv2.cvtColor(np.array(final_rgb), cv2.COLOR_RGB2BGR)

    def save_motion_photo(self, frame) -> Path:
        """
        Сохраняет фото при движении.
        """
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)

        annotated_frame = self.draw_motion_boxes(frame)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        photo_path = PHOTO_DIR / f"motion_{timestamp}.jpg"

        ok = cv2.imwrite(
            str(photo_path),
            annotated_frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
        )

        if not ok:
            raise RuntimeError("Не удалось сохранить фото движения через cv2.imwrite")

        print(f"[PHOTO] Saved motion photo: {photo_path}")
        self.cleanup_old_photos()
        return photo_path

    def save_snapshot_photo(self, frame) -> Path:
        """
        Сохраняет обычный контрольный снимок для ежечасного отчета.
        Это фото делается даже если движения не было.
        """
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)

        snapshot = frame.copy()
        timestamp_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cv2.putText(
            snapshot,
            f"SNAPSHOT {timestamp_text}",
            (20, snapshot.shape[0] - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        photo_path = PHOTO_DIR / f"snapshot_{timestamp}.jpg"

        ok = cv2.imwrite(
            str(photo_path),
            snapshot,
            [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
        )

        if not ok:
            raise RuntimeError("Не удалось сохранить контрольный снимок через cv2.imwrite")

        print(f"[SNAPSHOT] Saved hourly snapshot: {photo_path}")
        self.cleanup_old_photos()
        return photo_path

    def save_servo_command_photo(self, frame, command_text: str, result_text: str) -> Path:
        """
        Сохраняет фото после выполнения servo-команды.
        Тут используем Pillow, чтобы нормально отображалась кириллица.
        """
        if frame is None:
            raise RuntimeError("Нет кадра для фото после servo-команды")

        PHOTO_DIR.mkdir(parents=True, exist_ok=True)

        timestamp_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "Команда выполнена",
            f"Команда: {command_text}",
            f"Результат: {result_text}",
            timestamp_text,
        ]

        annotated = self.draw_russian_text_block(frame.copy(), lines)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        photo_path = PHOTO_DIR / f"servo_{timestamp}.jpg"

        ok = cv2.imwrite(
            str(photo_path),
            annotated,
            [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
        )

        if not ok:
            raise RuntimeError("Не удалось сохранить фото после servo-команды")

        print(f"[SERVO PHOTO] Saved servo command photo: {photo_path}")
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
