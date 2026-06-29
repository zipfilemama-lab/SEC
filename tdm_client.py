import os
import time
from pathlib import Path

import requests

from config import (
    TDM_TOKEN,
    WORKSPACE_ID,
    GROUP_ID,
    TDM_API_BASE,
    TDM_FILEUPLOAD_BASE,
)


class TDMClient:
    """
    Клиент TDM.

    Умеет:
    - отправлять текст;
    - отправлять фото;
    - читать непрочитанные сообщения;
    - подтверждать обработанные сообщения.
    """

    def __init__(
        self,
        token: str = TDM_TOKEN,
        workspace_id: int = WORKSPACE_ID,
        group_id: int = GROUP_ID,
        api_base: str = TDM_API_BASE,
        fileupload_base: str = TDM_FILEUPLOAD_BASE,
    ):
        self.token = token
        self.workspace_id = workspace_id
        self.group_id = group_id
        self.api_base = api_base.rstrip("/")
        self.fileupload_base = fileupload_base.rstrip("/")
        self.session = requests.Session()

    def _auth_headers(self) -> dict:
        return {
            "Authorization": self.token,
            "Accept": "application/json",
        }

    def send_text_message(self, message: str) -> bool:
        url = (
            f"{self.api_base}/botapi/v1/messages/sendTextMessage/"
            f"{self.workspace_id}/{self.group_id}"
        )

        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"

        payload = {
            "clientRandomId": int(time.time() * 1000),
            "message": message,
        }

        try:
            response = self.session.post(
                url,
                headers=headers,
                json=payload,
                timeout=30,
            )

            print("[TDM TEXT] STATUS:", response.status_code)
            print("[TDM TEXT] RESPONSE:", response.text[:300])

            response.raise_for_status()
            return True

        except Exception as error:
            print("[TDM TEXT ERROR]", error)
            return False

    def get_all_unread_messages(self) -> list[dict]:
        """
        Получает непрочитанные сообщения из текущей группы.

        Это простой тестовый вариант.
        Для боевой версии лучше перейти на SSE.
        """
        url = (
            f"{self.api_base}/botapi/v1/messages/getAllUnreadMessages/"
            f"{self.workspace_id}/{self.group_id}"
        )

        headers = self._auth_headers()

        response = self.session.post(
            url,
            headers=headers,
            timeout=30,
        )

        print("[TDM GET UNREAD] STATUS:", response.status_code)

        response.raise_for_status()

        if not response.text.strip():
            return []

        data = response.json()

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            return [data]

        return []

    def confirm_messages(self, last_message_id: int) -> bool:
        """
        Подтверждает, что сообщения до last_message_id обработаны.
        """
        url = (
            f"{self.api_base}/botapi/v1/messages/confirm/"
            f"{self.workspace_id}/{self.group_id}"
        )

        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"

        payload = {
            "lastMessageId": int(last_message_id),
        }

        try:
            response = self.session.post(
                url,
                headers=headers,
                json=payload,
                timeout=30,
            )

            print("[TDM CONFIRM] STATUS:", response.status_code)

            response.raise_for_status()
            return True

        except Exception as error:
            print("[TDM CONFIRM ERROR]", error)
            return False

    def upload_image(self, image_path: str | Path) -> dict:
        image_path = Path(image_path)

        if not image_path.exists():
            raise FileNotFoundError(f"Файл не найден: {image_path}")

        url = f"{self.fileupload_base}/api/v1/upload/image/encryptable"

        headers = self._auth_headers()

        # Для TDM имя файла лучше давать латиницей.
        upload_filename = "motion.jpg"

        with image_path.open("rb") as file_object:
            files = {
                "file": (upload_filename, file_object, "image/jpeg")
            }

            response = self.session.post(
                url,
                headers=headers,
                files=files,
                timeout=60,
            )

        print("[TDM UPLOAD] STATUS:", response.status_code)
        print("[TDM UPLOAD] RESPONSE:", response.text[:300])

        response.raise_for_status()
        return response.json()

    def send_image_message(
        self,
        image_path: str | Path,
        upload_result: dict,
        message: str = "Обнаружено движение",
    ) -> dict:
        image_path = Path(image_path)

        original = upload_result.get("original")
        if not original:
            raise RuntimeError(f"В upload-ответе нет поля original: {upload_result}")

        resource = original.get("resource")
        if not resource:
            raise RuntimeError(f"В upload-ответе нет поля original.resource: {upload_result}")

        url = (
            f"{self.api_base}/botapi/v1/messages/sendImage/"
            f"{self.workspace_id}/{self.group_id}"
        )

        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"

        payload = {
            "image": {
                "fileName": "motion.jpg",
                "width": original.get("width", 1280),
                "height": original.get("height", 720),
                "length": os.path.getsize(image_path),
                "resourceRef": {
                    "id": resource["id"],
                    "url": resource["url"],
                    "key": resource["key"],
                    "transformation": resource["transformation"],
                },
                "mimeType": "image/jpg",
            },
            "clientRandomId": int(time.time() * 1000),
            "message": message,
        }

        response = self.session.post(
            url,
            headers=headers,
            json=payload,
            timeout=60,
        )

        print("[TDM SEND IMAGE] STATUS:", response.status_code)
        print("[TDM SEND IMAGE] RESPONSE:", response.text[:300])

        response.raise_for_status()

        try:
            return response.json()
        except Exception:
            return {"raw_response": response.text}

    def send_image(
        self,
        image_path: str | Path,
        message: str = "Обнаружено движение",
    ) -> bool:
        try:
            upload_result = self.upload_image(image_path)
            self.send_image_message(image_path, upload_result, message)
            print("[TDM] Image sent")
            return True

        except Exception as error:
            print("[TDM IMAGE ERROR]", error)
            return False
