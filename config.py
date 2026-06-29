import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def load_env_file(env_path: Path = BASE_DIR / ".env") -> None:
    """
    Простой загрузчик .env без дополнительных библиотек.

    Пример .env:
    TDM_TOKEN=BOT-xxxx
    WORKSPACE_ID=-1
    GROUP_ID=123456789
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

    return value.strip().lower() in {"1", "true", "yes", "on"}


# ============================================================
# TDM SETTINGS
# ============================================================

TDM_TOKEN = os.getenv("TDM_TOKEN")
WORKSPACE_ID = get_env_int("WORKSPACE_ID", -1)
GROUP_ID = get_env_int("GROUP_ID", 0)

TDM_API_BASE = os.getenv("TDM_API_BASE", "https://api.tdm.mos.ru")
TDM_FILEUPLOAD_BASE = os.getenv("TDM_FILEUPLOAD_BASE", "https://fileupload.tdm.mos.ru")


# ============================================================
# PROJECT PATHS
# ============================================================

DATA_DIR = BASE_DIR / "data"
PHOTO_DIR = DATA_DIR / "motion_events"
LOG_DIR = DATA_DIR / "logs"


# ============================================================
# CAMERA / MOTION SETTINGS
# ============================================================

MAX_PHOTOS = get_env_int("MAX_PHOTOS", 50)

CAMERA_INDEX = get_env_int("CAMERA_INDEX", 0)
CAMERA_WIDTH = get_env_int("CAMERA_WIDTH", 1280)
CAMERA_HEIGHT = get_env_int("CAMERA_HEIGHT", 720)
CAMERA_FPS = get_env_int("CAMERA_FPS", 15)

JPEG_QUALITY = get_env_int("JPEG_QUALITY", 80)

MOTION_AREA_THRESHOLD = get_env_int("MOTION_AREA_THRESHOLD", 2500)
MOTION_RESIZE_SCALE = get_env_float("MOTION_RESIZE_SCALE", 0.5)

SEND_COOLDOWN_SECONDS = get_env_int("SEND_COOLDOWN_SECONDS", 5)


# ============================================================
# WIFI SCANNER SETTINGS
# ============================================================

WIFI_INTERFACE = os.getenv("WIFI_INTERFACE", "wlan1")
WIFI_SCAN_INTERVAL_SECONDS = get_env_int("WIFI_SCAN_INTERVAL_SECONDS", 600)


# ============================================================
# SERVO / PCA9685 SETTINGS
# ============================================================

SERVO_ENABLED = get_env_bool("SERVO_ENABLED", True)

SERVO_I2C_BUS = get_env_int("SERVO_I2C_BUS", 1)
SERVO_I2C_ADDRESS = int(os.getenv("SERVO_I2C_ADDRESS", "0x40"), 16)

SERVO_1_CHANNEL = get_env_int("SERVO_1_CHANNEL", 0)
SERVO_2_CHANNEL = get_env_int("SERVO_2_CHANNEL", 1)

SERVO_CENTER_ANGLE = get_env_int("SERVO_CENTER_ANGLE", 90)

# Безопасный диапазон углов.
# Если механизм может упираться, лучше поставить 30 и 150.
SERVO_MIN_ANGLE = get_env_int("SERVO_MIN_ANGLE", 0)
SERVO_MAX_ANGLE = get_env_int("SERVO_MAX_ANGLE", 180)

# Углы для будущей автоматической реакции на камеру.
# Сейчас камера НЕ двигает серво автоматически.
SERVO_1_ALERT_ANGLE = get_env_int("SERVO_1_ALERT_ANGLE", 150)
SERVO_2_ALERT_ANGLE = get_env_int("SERVO_2_ALERT_ANGLE", 30)

SERVO_STEP_DELAY = get_env_float("SERVO_STEP_DELAY", 0.02)
SERVO_HOLD_SECONDS = get_env_float("SERVO_HOLD_SECONDS", 0.5)

# Ограничения, чтобы сервоприводы и провода не грелись.
SERVO_COMMAND_COOLDOWN_SECONDS = get_env_int("SERVO_COMMAND_COOLDOWN_SECONDS", 15)
SERVO_MAX_COMMANDS_PER_MINUTE = get_env_int("SERVO_MAX_COMMANDS_PER_MINUTE", 4)

# После движения отключаем PWM.
# Серво перестаёт активно удерживать позицию и меньше греется.
SERVO_AUTO_OFF_AFTER_MOVE = get_env_bool("SERVO_AUTO_OFF_AFTER_MOVE", True)


# ============================================================
# HELPERS
# ============================================================

def ensure_project_dirs() -> None:
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def validate_config() -> None:
    """
    Проверяем самые важные настройки перед запуском.
    """
    if not TDM_TOKEN or TDM_TOKEN == "ВСТАВЬ_СЮДА_СВОЙ_TDM_TOKEN":
        raise RuntimeError("Не найден TDM_TOKEN. Проверь файл .env")

    if GROUP_ID == 0:
        raise RuntimeError("Не найден GROUP_ID. Проверь файл .env")

    if SERVO_MIN_ANGLE < 0 or SERVO_MAX_ANGLE > 180:
        raise RuntimeError("SERVO_MIN_ANGLE и SERVO_MAX_ANGLE должны быть в диапазоне 0–180")

    if SERVO_MIN_ANGLE >= SERVO_MAX_ANGLE:
        raise RuntimeError("SERVO_MIN_ANGLE должен быть меньше SERVO_MAX_ANGLE")
