import time
from datetime import datetime

from tdm_client import TDMClient


TEMP_FILE = "/sys/class/thermal/thermal_zone0/temp"
SEND_INTERVAL_SECONDS = 30


def read_cpu_temperature() -> float:
    """
    Читает температуру процессора Raspberry Pi.

    В Linux температура обычно лежит в файле:
    /sys/class/thermal/thermal_zone0/temp

    Там число хранится не как 48.5, а как 48500.
    То есть температура записана в тысячных долях градуса.

    Поэтому мы делим на 1000.
    """
    with open(TEMP_FILE, "r") as file:
        raw_value = file.read().strip()

    temperature_c = int(raw_value) / 1000
    return temperature_c


def main():
    print("====================================")
    print(" Temperature test every 30 seconds")
    print("====================================")

    tdm_client = TDMClient()

    while True:
        try:
            temperature = read_cpu_temperature()
            now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

            message = (
                "🌡 Тест температуры Raspberry Pi\n"
                f"Время: {now}\n"
                f"CPU: {temperature:.1f} °C\n"
                "Интервал теста: 30 секунд"
            )

            print("[TEMP]", message)

            ok = tdm_client.send_text_message(message)

            if ok:
                print("[TDM] Temperature message sent")
            else:
                print("[TDM] Failed to send temperature message")

        except Exception as error:
            print("[ERROR]", error)

        time.sleep(SEND_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
