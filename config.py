import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def load_env_file(env_path: Path = BASE_DIR / ".env") -> None:
    """
    Загружает переменные из файла .env.

    Поддерживаемый формат:
        VARIABLE=value
        VARIABLE="value"
    """
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        os.environ.setdefault(key, value)


load_env_file()


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or value == "":
        return default

    return int(value)


def get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None or value == "":
        return default

    return float(value)


def get_env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None or value == "":
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ============================================================
# TDM
# ============================================================

TDM_TOKEN = os.getenv("TDM_TOKEN")
WORKSPACE_ID = get_env_int("WORKSPACE_ID", -1)
GROUP_ID = get_env_int("GROUP_ID", 0)

TDM_API_BASE = os.getenv(
    "TDM_API_BASE",
    "https://api.tdm.mos.ru",
)

TDM_FILEUPLOAD_BASE = os.getenv(
    "TDM_FILEUPLOAD_BASE",
    "https://fileupload.tdm.mos.ru",
)


# ============================================================
# ПАПКИ ПРОЕКТА
# ============================================================

DATA_DIR = BASE_DIR / "data"
PHOTO_DIR = DATA_DIR / "motion_events"
LOG_DIR = DATA_DIR / "logs"


# ============================================================
# ОБЩИЕ НАСТРОЙКИ КАМЕР
# ============================================================

JPEG_QUALITY = get_env_int("JPEG_QUALITY", 80)
MAX_PHOTOS = get_env_int("MAX_PHOTOS", 100)

SEND_COOLDOWN_SECONDS = get_env_int(
    "SEND_COOLDOWN_SECONDS",
    5,
)

MOTION_CONFIRM_FRAMES = get_env_int(
    "MOTION_CONFIRM_FRAMES",
    3,
)


# ============================================================
# RASPBERRY PI CAMERA
# ============================================================

RPI_CAMERA_ENABLED = get_env_bool(
    "RPI_CAMERA_ENABLED",
    True,
)

RPI_CAMERA_WIDTH = get_env_int(
    "RPI_CAMERA_WIDTH",
    1280,
)

RPI_CAMERA_HEIGHT = get_env_int(
    "RPI_CAMERA_HEIGHT",
    720,
)

RPI_CAMERA_FPS = get_env_int(
    "RPI_CAMERA_FPS",
    15,
)

RPI_MOTION_AREA_THRESHOLD = get_env_int(
    "RPI_MOTION_AREA_THRESHOLD",
    2500,
)

RPI_MOTION_RESIZE_SCALE = get_env_float(
    "RPI_MOTION_RESIZE_SCALE",
    0.5,
)


# ============================================================
# USB CAMERA
# ============================================================

USB_CAMERA_ENABLED = get_env_bool(
    "USB_CAMERA_ENABLED",
    True,
)

USB_CAMERA_DEVICE = os.getenv(
    "USB_CAMERA_DEVICE",
    "/dev/video8",
)

USB_CAMERA_WIDTH = get_env_int(
    "USB_CAMERA_WIDTH",
    1280,
)

USB_CAMERA_HEIGHT = get_env_int(
    "USB_CAMERA_HEIGHT",
    720,
)

USB_CAMERA_FPS = get_env_int(
    "USB_CAMERA_FPS",
    15,
)

USB_MOTION_AREA_THRESHOLD = get_env_int(
    "USB_MOTION_AREA_THRESHOLD",
    2500,
)

USB_MOTION_RESIZE_SCALE = get_env_float(
    "USB_MOTION_RESIZE_SCALE",
    0.5,
)


# Старые названия оставлены для совместимости
CAMERA_INDEX = get_env_int("CAMERA_INDEX", 0)
CAMERA_WIDTH = RPI_CAMERA_WIDTH
CAMERA_HEIGHT = RPI_CAMERA_HEIGHT
CAMERA_FPS = RPI_CAMERA_FPS
MOTION_AREA_THRESHOLD = RPI_MOTION_AREA_THRESHOLD
MOTION_RESIZE_SCALE = RPI_MOTION_RESIZE_SCALE


# ============================================================
# WI-FI
# ============================================================

WIFI_INTERFACE = os.getenv(
    "WIFI_INTERFACE",
    "wlan1",
)

WIFI_SCAN_INTERVAL_SECONDS = get_env_int(
    "WIFI_SCAN_INTERVAL_SECONDS",
    600,
)


# ============================================================
# СЕРВОПРИВОДЫ
# ============================================================

SERVO_ENABLED = get_env_bool(
    "SERVO_ENABLED",
    False,
)

SERVO_I2C_BUS = get_env_int(
    "SERVO_I2C_BUS",
    1,
)

SERVO_I2C_ADDRESS = int(
    os.getenv("SERVO_I2C_ADDRESS", "0x40"),
    16,
)

SERVO_1_CHANNEL = get_env_int(
    "SERVO_1_CHANNEL",
    0,
)

SERVO_2_CHANNEL = get_env_int(
    "SERVO_2_CHANNEL",
    1,
)

SERVO_CENTER_ANGLE = get_env_int(
    "SERVO_CENTER_ANGLE",
    90,
)

SERVO_MIN_ANGLE = get_env_int(
    "SERVO_MIN_ANGLE",
    30,
)

SERVO_MAX_ANGLE = get_env_int(
    "SERVO_MAX_ANGLE",
    150,
)

SERVO_1_ALERT_ANGLE = get_env_int(
    "SERVO_1_ALERT_ANGLE",
    150,
)

SERVO_2_ALERT_ANGLE = get_env_int(
    "SERVO_2_ALERT_ANGLE",
    30,
)

SERVO_STEP_DELAY = get_env_float(
    "SERVO_STEP_DELAY",
    0.02,
)

SERVO_HOLD_SECONDS = get_env_float(
    "SERVO_HOLD_SECONDS",
    0.5,
)

SERVO_COMMAND_COOLDOWN_SECONDS = get_env_int(
    "SERVO_COMMAND_COOLDOWN_SECONDS",
    15,
)

SERVO_MAX_COMMANDS_PER_MINUTE = get_env_int(
    "SERVO_MAX_COMMANDS_PER_MINUTE",
    4,
)

SERVO_AUTO_OFF_AFTER_MOVE = get_env_bool(
    "SERVO_AUTO_OFF_AFTER_MOVE",
    True,
)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def ensure_project_dirs() -> None:
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def validate_config() -> None:
    if (
        not TDM_TOKEN
        or TDM_TOKEN == "ВСТАВЬ_СЮДА_СВОЙ_TDM_TOKEN"
    ):
        raise RuntimeError(
            "Не найден TDM_TOKEN. Проверь файл .env"
        )

    if GROUP_ID == 0:
        raise RuntimeError(
            "Не найден GROUP_ID. Проверь файл .env"
        )

    if not RPI_CAMERA_ENABLED and not USB_CAMERA_ENABLED:
        raise RuntimeError(
            "Обе камеры отключены в .env"
        )

    if USB_CAMERA_ENABLED and not USB_CAMERA_DEVICE:
        raise RuntimeError(
            "Не указан USB_CAMERA_DEVICE"
        )

    if SEND_COOLDOWN_SECONDS < 1:
        raise RuntimeError(
            "SEND_COOLDOWN_SECONDS должен быть не меньше 1"
        )

    if MOTION_CONFIRM_FRAMES < 1:
        raise RuntimeError(
            "MOTION_CONFIRM_FRAMES должен быть не меньше 1"
        )

    if SERVO_MIN_ANGLE < 0 or SERVO_MAX_ANGLE > 180:
        raise RuntimeError(
            "Углы сервоприводов должны быть в диапазоне 0–180"
        )

    if SERVO_MIN_ANGLE >= SERVO_MAX_ANGLE:
        raise RuntimeError(
            "SERVO_MIN_ANGLE должен быть меньше SERVO_MAX_ANGLE"
        )
PY
