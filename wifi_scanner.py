import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class WiFiNetwork:
    ssid: str
    channel: str
    frequency: str
    signal: int
    security: str


class WiFiScannerReporter:
    """
    Сканирует Wi-Fi сети через Alfa адаптер и раз в час отправляет отчет в TDM.

    Логика:
    - Alfa адаптер работает как wlan1;
    - каждые N секунд запускаем nmcli;
    - сохраняем найденные сети и силу сигнала;
    - в начале нового часа отправляем отчет;
    - сравниваем текущий час с прошлым часом.
    """

    def __init__(
        self,
        tdm_client,
        interface: str = "wlan1",
        scan_interval_seconds: int = 600,
    ):
        self.tdm_client = tdm_client
        self.interface = interface
        self.scan_interval_seconds = scan_interval_seconds

        # Данные прошлого часа нужны, чтобы понять:
        # какие сети появились, а какие пропали.
        self.previous_hour_summary = {}

    def scan_wifi_networks(self) -> list[WiFiNetwork]:
        """
        Делает один скан Wi-Fi сетей.

        Используем nmcli, потому что ты уже проверил команду:
        nmcli dev wifi list ifname wlan1 --rescan yes
        """

        command = [
            "nmcli",
            "-t",
            "-f",
            "SSID,CHAN,FREQ,SIGNAL,SECURITY",
            "dev",
            "wifi",
            "list",
            "ifname",
            self.interface,
            "--rescan",
            "yes",
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=40,
                check=True,
            )
        except subprocess.TimeoutExpired:
            print("[WIFI SCANNER ERROR] nmcli timeout")
            return []
        except subprocess.CalledProcessError as error:
            print("[WIFI SCANNER ERROR] nmcli failed")
            print(error.stderr)
            return []
        except Exception as error:
            print("[WIFI SCANNER ERROR]", error)
            return []

        networks = []

        for line in result.stdout.splitlines():
            line = line.strip()

            if not line:
                continue

            parts = line.split(":")

            # Ожидаем 5 полей:
            # SSID:CHAN:FREQ:SIGNAL:SECURITY
            if len(parts) < 5:
                continue

            ssid = parts[0].strip()
            channel = parts[1].strip()
            frequency = parts[2].strip()
            signal_raw = parts[3].strip()
            security = ":".join(parts[4:]).strip()

            if not ssid:
                ssid = "<hidden>"

            try:
                signal = int(signal_raw)
            except ValueError:
                continue

            networks.append(
                WiFiNetwork(
                    ssid=ssid,
                    channel=channel,
                    frequency=frequency,
                    signal=signal,
                    security=security if security else "unknown",
                )
            )

        # Если одна и та же сеть видна несколько раз,
        # например через несколько точек доступа,
        # оставляем самую сильную версию.
        strongest_by_ssid = {}

        for network in networks:
            old = strongest_by_ssid.get(network.ssid)
            if old is None or network.signal > old.signal:
                strongest_by_ssid[network.ssid] = network

        return list(strongest_by_ssid.values())

    def add_scan_to_hour_data(
        self,
        hour_data: dict,
        networks: list[WiFiNetwork],
    ) -> None:
        """
        Добавляет один скан в статистику текущего часа.
        """

        now = datetime.now()

        for network in networks:
            if network.ssid not in hour_data:
                hour_data[network.ssid] = {
                    "ssid": network.ssid,
                    "first_seen": now,
                    "last_seen": now,
                    "first_signal": network.signal,
                    "last_signal": network.signal,
                    "signals": [],
                    "channels": set(),
                    "frequencies": set(),
                    "securities": set(),
                }

            item = hour_data[network.ssid]
            item["last_seen"] = now
            item["last_signal"] = network.signal
            item["signals"].append(network.signal)
            item["channels"].add(network.channel)
            item["frequencies"].add(network.frequency)
            item["securities"].add(network.security)

    def build_hour_summary(self, hour_data: dict) -> dict:
        """
        Превращает сырые данные за час в краткую статистику.
        """

        summary = {}

        for ssid, item in hour_data.items():
            signals = item["signals"]

            if not signals:
                continue

            avg_signal = sum(signals) / len(signals)
            min_signal = min(signals)
            max_signal = max(signals)

            summary[ssid] = {
                "ssid": ssid,
                "count": len(signals),
                "first_signal": item["first_signal"],
                "last_signal": item["last_signal"],
                "avg_signal": avg_signal,
                "min_signal": min_signal,
                "max_signal": max_signal,
                "channels": sorted(item["channels"]),
                "frequencies": sorted(item["frequencies"]),
                "securities": sorted(item["securities"]),
            }

        return summary

    def signal_comment(self, delta: float) -> str:
        """
        Объясняет изменение сигнала простыми словами.

        SIGNAL в nmcli — это проценты.
        Больше процентов = лучше сигнал.
        """

        if delta >= 15:
            return "сильно усилился"
        if delta >= 5:
            return "усилился"
        if delta <= -15:
            return "сильно ослаб"
        if delta <= -5:
            return "ослаб"
        return "почти не изменился"

    def build_report_message(
    self,
    hour_start: datetime,
    hour_end: datetime,
    summary: dict,    
    ) -> str:
   
    """
    Создает красивый Wi-Fi отчет для TDM.

    Формат:
    - короткая шапка;
    - новые сети;
    - пропавшие сети;
    - сильный сигнал;
    - средний сигнал;
    - слабый сигнал;
    - итог.
    """

    current_ssids = set(summary.keys())
    previous_ssids = set(self.previous_hour_summary.keys())

    new_networks = sorted(current_ssids - previous_ssids)
    disappeared_networks = sorted(previous_ssids - current_ssids)

    def network_signal_group(item):
        """
        Делим сети по среднему сигналу.
        nmcli SIGNAL показывает проценты.
        """
        avg_signal = item["avg_signal"]

        if avg_signal >= 70:
            return "strong"
        if avg_signal >= 40:
            return "medium"
        return "weak"

    def signal_emoji(avg_signal):
        if avg_signal >= 70:
            return "🟢"
        if avg_signal >= 40:
            return "🟡"
        return "🔴"

    def change_emoji(delta):
        if delta >= 5:
            return "⬆️"
        if delta <= -5:
            return "⬇️"
        return "➖"

    def clean_list(values):
        """
        Красиво склеивает каналы/частоты/защиту.
        """
        values = [str(v) for v in values if str(v).strip()]
        if not values:
            return "unknown"
        return "/".join(values)

    def format_network(item):
        """
        Формат одной сети.
        Вместо длинной строки делаем 4 короткие строки.
        """

        ssid = item["ssid"]
        avg_signal = item["avg_signal"]
        min_signal = item["min_signal"]
        max_signal = item["max_signal"]

        delta_inside_hour = item["last_signal"] - item["first_signal"]

        channels = clean_list(item["channels"])
        security = clean_list(item["securities"])
        count = item["count"]

        previous = self.previous_hour_summary.get(ssid)

        if previous:
            delta_vs_previous_hour = avg_signal - previous["avg_signal"]
            previous_line = (
                f"{change_emoji(delta_vs_previous_hour)} к прошлому часу: "
                f"{delta_vs_previous_hour:+.1f}%"
            )
        else:
            previous_line = "🆕 новая сеть"

        return (
            f"{signal_emoji(avg_signal)} {ssid}\n"
            f"   📶 сигнал: {avg_signal:.0f}% средний | {min_signal}–{max_signal}%\n"
            f"   📍 канал: {channels} | 🔐 {security}\n"
            f"   📊 замеров: {count} | за час: {delta_inside_hour:+d}%\n"
            f"   {previous_line}"
        )

    strong_networks = []
    medium_networks = []
    weak_networks = []

    for item in summary.values():
        group = network_signal_group(item)

        if group == "strong":
            strong_networks.append(item)
        elif group == "medium":
            medium_networks.append(item)
        else:
            weak_networks.append(item)

    strong_networks = sorted(
        strong_networks,
        key=lambda item: item["avg_signal"],
        reverse=True,
    )

    medium_networks = sorted(
        medium_networks,
        key=lambda item: item["avg_signal"],
        reverse=True,
    )

    weak_networks = sorted(
        weak_networks,
        key=lambda item: item["avg_signal"],
        reverse=True,
    )

    strengthened_count = 0
    weakened_count = 0
    stable_count = 0

    for item in summary.values():
        delta = item["last_signal"] - item["first_signal"]

        if delta >= 5:
            strengthened_count += 1
        elif delta <= -5:
            weakened_count += 1
        else:
            stable_count += 1

    lines = []

    lines.append("📡 Wi-Fi отчет Raspberry Pi")
    lines.append("")
    lines.append(f"🧩 Адаптер: Alfa {self.interface}")
    lines.append(f"🕐 Период: {hour_start.strftime('%H:%M')}–{hour_end.strftime('%H:%M')}")
    lines.append(f"📍 Найдено сетей: {len(summary)}")
    lines.append("")

    if new_networks:
        lines.append("🆕 Новые сети:")
        for ssid in new_networks[:10]:
            lines.append(f"   + {ssid}")

        if len(new_networks) > 10:
            lines.append(f"   …и еще {len(new_networks) - 10}")

        lines.append("")

    if disappeared_networks:
        lines.append("❌ Пропали сети:")
        for ssid in disappeared_networks[:10]:
            lines.append(f"   - {ssid}")

        if len(disappeared_networks) > 10:
            lines.append(f"   …и еще {len(disappeared_networks) - 10}")

        lines.append("")
    else:
        lines.append("❌ Пропали сети: 0")
        lines.append("")

    if not summary:
        lines.append("Сети за этот час не найдены.")
        return "\n".join(lines)

    if strong_networks:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("🟢 Сильный сигнал")
        lines.append("━━━━━━━━━━━━━━")

        for item in strong_networks[:5]:
            lines.append(format_network(item))
            lines.append("")

    if medium_networks:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("🟡 Средний сигнал")
        lines.append("━━━━━━━━━━━━━━")

        for item in medium_networks[:7]:
            lines.append(format_network(item))
            lines.append("")

    if weak_networks:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("🔴 Слабый сигнал")
        lines.append("━━━━━━━━━━━━━━")

        for item in weak_networks[:7]:
            lines.append(format_network(item))
            lines.append("")

        if len(weak_networks) > 7:
            lines.append(f"…и еще слабых сетей: {len(weak_networks) - 7}")
            lines.append("")

    lines.append("━━━━━━━━━━━━━━")
    lines.append("📊 Итог")
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"⬆️ Усилились: {strengthened_count}")
    lines.append(f"⬇️ Ослабли: {weakened_count}")
    lines.append(f"➖ Почти без изменений: {stable_count}")
    lines.append(f"🆕 Новые: {len(new_networks)}")
    lines.append(f"❌ Пропали: {len(disappeared_networks)}")

    return "\n".join(lines)

    def send_hourly_report(
        self,
        hour_start: datetime,
        hour_end: datetime,
        hour_data: dict,
    ) -> None:
        """
        Собирает отчет и отправляет его в TDM.
        """

        summary = self.build_hour_summary(hour_data)
        message = self.build_report_message(hour_start, hour_end, summary)

        print("[WIFI SCANNER] Sending hourly Wi-Fi report")
        self.tdm_client.send_text_message(message)

        # После отправки текущий час становится прошлым часом.
        self.previous_hour_summary = summary

    def run_forever(self, stop_event) -> None:
        """
        Главный цикл Wi-Fi сканера.
        Работает в отдельном потоке и не мешает камере.
        """

        print(f"[WIFI SCANNER] Started on interface {self.interface}")

        current_hour_start = datetime.now().replace(minute=0, second=0, microsecond=0)
        next_hour_start = current_hour_start + timedelta(hours=1)

        hour_data = {}

        while not stop_event.is_set():
            now = datetime.now()

            if now >= next_hour_start:
                self.send_hourly_report(
                    hour_start=current_hour_start,
                    hour_end=next_hour_start,
                    hour_data=hour_data,
                )

                current_hour_start = next_hour_start
                next_hour_start = current_hour_start + timedelta(hours=1)
                hour_data = {}

            networks = self.scan_wifi_networks()
            self.add_scan_to_hour_data(hour_data, networks)

            print(f"[WIFI SCANNER] Found {len(networks)} networks")

            seconds_until_next_hour = (next_hour_start - datetime.now()).total_seconds()
            sleep_seconds = min(
                self.scan_interval_seconds,
                max(1, seconds_until_next_hour),
            )

            stop_event.wait(sleep_seconds)

        print("[WIFI SCANNER] Stopped")
