import time
from datetime import datetime, timedelta


class CPUStatsReporter:
    """
    Собирает температуру CPU и раз в час отправляет среднюю температуру в TDM.

    Логика:
    - каждую минуту читает температуру CPU;
    - складывает значения в список;
    - ровно в начале нового часа отправляет среднее значение за прошлый час.
    """

    def __init__(self, tdm_client, sample_interval_seconds: int = 60):
        self.tdm_client = tdm_client
        self.sample_interval_seconds = sample_interval_seconds

    def read_cpu_temp(self) -> float | None:
        """
        Читает температуру CPU Raspberry Pi.

        Файл:
        /sys/class/thermal/thermal_zone0/temp

        Обычно там число типа:
        52375

        Это значит:
        52.375 °C
        """
        temp_path = "/sys/class/thermal/thermal_zone0/temp"

        try:
            with open(temp_path, "r", encoding="utf-8") as file:
                raw_value = file.read().strip()

            return int(raw_value) / 1000.0

        except Exception as error:
            print("[CPU STATS ERROR] Не удалось прочитать температуру:", error)
            return None

    def send_hourly_report(self, hour_start: datetime, hour_end: datetime, temperatures: list[float]) -> None:
        """
        Отправляет сообщение в TDM.
        """
        if not temperatures:
            message = (
                "🌡 Статистика CPU Raspberry Pi\n\n"
                f"Период: {hour_start.strftime('%H:%M')}–{hour_end.strftime('%H:%M')}\n"
                "Нет данных по температуре."
            )

            self.tdm_client.send_text_message(message)
            return

        avg_temp = sum(temperatures) / len(temperatures)
        max_temp = max(temperatures)
        min_temp = min(temperatures)

        message = (
            "🌡 Статистика CPU Raspberry Pi\n\n"
            f"Период: {hour_start.strftime('%H:%M')}–{hour_end.strftime('%H:%M')}\n"
            f"Средняя температура: {avg_temp:.1f} °C\n"
            f"Минимальная температура: {min_temp:.1f} °C\n"
            f"Максимальная температура: {max_temp:.1f} °C\n"
            f"Количество замеров: {len(temperatures)}"
        )

        print("[CPU STATS] Sending hourly report")
        self.tdm_client.send_text_message(message)

    def run_forever(self, stop_event) -> None:
        """
        Главный цикл статистики.

        Работает в отдельном потоке, чтобы не мешать камере.
        """
        print("[CPU STATS] Started")

        current_hour_start = datetime.now().replace(minute=0, second=0, microsecond=0)
        next_hour_start = current_hour_start + timedelta(hours=1)

        temperatures = []

        while not stop_event.is_set():
            now = datetime.now()

            # Если наступил новый час — отправляем отчет за прошлый час.
            if now >= next_hour_start:
                self.send_hourly_report(
                    hour_start=current_hour_start,
                    hour_end=next_hour_start,
                    temperatures=temperatures,
                )

                current_hour_start = next_hour_start
                next_hour_start = current_hour_start + timedelta(hours=1)
                temperatures = []

            temp = self.read_cpu_temp()

            if temp is not None:
                temperatures.append(temp)
                print(f"[CPU STATS] Current CPU temp: {temp:.1f} °C")

            # Спим либо до следующего замера, либо до начала нового часа.
            seconds_until_next_hour = (next_hour_start - datetime.now()).total_seconds()
            sleep_seconds = min(self.sample_interval_seconds, max(1, seconds_until_next_hour))

            stop_event.wait(sleep_seconds)

        print("[CPU STATS] Stopped")
