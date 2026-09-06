"""Cliente REST para LVS Voice Service v3.

El nombre se conserva durante la migración desde v7, pero no usa WebSocket.
Cada solicitud recibe una única respuesta JSON del endpoint /voice/process.
"""

import base64
import logging
import threading
import time

import requests
from PySide6.QtCore import QObject, Signal


logger = logging.getLogger("lvs-desktop")


class VoiceApiClient(QObject):
    request_succeeded = Signal(int, object)
    request_failed = Signal(int, str)
    transcription_ready = Signal(int, str)
    health_changed = Signal(bool, str)

    def __init__(self, url: str, timeout_seconds: int = 150):
        super().__init__()
        self.url = url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._next_request_id = 0

    def check_health(self) -> None:
        threading.Thread(target=self._check_health, daemon=True).start()

    def _check_health(self) -> None:
        started = time.perf_counter()
        try:
            response = requests.get(f"{self.url}/health", timeout=10)
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.debug("[API] Healthcheck status=%s duration_ms=%.1f", response.status_code, elapsed_ms)
            if response.ok:
                self.health_changed.emit(True, "Servicio de voz conectado.")
            else:
                self.health_changed.emit(False, f"Servicio de voz no disponible ({response.status_code}).")
        except requests.RequestException as exc:
            logger.debug("[API] Healthcheck falló duration_ms=%.1f", (time.perf_counter() - started) * 1000)
            self.health_changed.emit(False, f"No se pudo contactar al servicio de voz: {exc}")

    def send_text(self, text: str) -> int:
        return self._start_request(text=text)

    def send_voice(self, audio_bytes: bytes, tts_enabled: bool) -> int:
        return self._start_request(audio_bytes=audio_bytes, tts_enabled=tts_enabled)

    def _start_request(
        self,
        *,
        text: str | None = None,
        audio_bytes: bytes | None = None,
        tts_enabled: bool = False,
    ) -> int:
        with self._lock:
            self._next_request_id += 1
            request_id = self._next_request_id

        logger.debug(
            "[API] Solicitud iniciada request_id=%s type=%s chars=%s audio_bytes=%s tts=%s",
            request_id,
            "voice" if audio_bytes is not None else "text",
            len(text or ""),
            len(audio_bytes or b""),
            tts_enabled,
        )

        if audio_bytes is not None:
            target = self._process_voice_request
            args = (request_id, audio_bytes, tts_enabled)
        else:
            target = self._process_text_request
            args = (request_id, text, False)
        threading.Thread(target=target, args=args, daemon=True).start()
        return request_id

    @staticmethod
    def _response_or_error(response):
        if response.ok:
            return response.json()
        detail = response.text.strip()
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise RuntimeError(f"Servicio de voz ({response.status_code}): {detail or 'sin detalle'}")

    def _process_text_request(
        self,
        request_id: int,
        text: str | None,
        synthesize_audio: bool,
    ) -> None:
        started = time.perf_counter()
        try:
            if not text:
                raise ValueError("La solicitud no contiene texto ni audio.")
            data = {"text": text}
            if synthesize_audio:
                data["tts"] = "true"

            response = requests.post(
                f"{self.url}/voice/process",
                data=data,
                timeout=self.timeout_seconds,
            )
            result = self._response_or_error(response)
            encoded_audio = result.get("response_audio_b64")
            if encoded_audio:
                result["_audio_bytes"] = base64.b64decode(encoded_audio)
            result["_client_total_ms"] = round((time.perf_counter() - started) * 1000, 1)
            logger.debug(
                "[API] Solicitud completada request_id=%s status=%s duration_ms=%.1f response_chars=%s audio_bytes=%s",
                request_id,
                response.status_code,
                result["_client_total_ms"],
                len(result.get("response_text", "")),
                len(result.get("_audio_bytes", b"")),
            )
            self.request_succeeded.emit(request_id, result)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            logger.debug("[API] Solicitud fallida request_id=%s duration_ms=%.1f", request_id, (time.perf_counter() - started) * 1000)
            self.request_failed.emit(request_id, str(exc))

    def _process_voice_request(self, request_id: int, audio_bytes: bytes, tts_enabled: bool) -> None:
        started = time.perf_counter()
        try:
            transcription_response = requests.post(
                f"{self.url}/voice/transcribe",
                files={"audio": ("capture.wav", audio_bytes, "audio/wav")},
                timeout=self.timeout_seconds,
            )
            transcription = self._response_or_error(transcription_response).get("input_text", "").strip()
            logger.debug(
                "[STT] Transcripción recibida request_id=%s status=%s duration_ms=%.1f chars=%s",
                request_id,
                transcription_response.status_code,
                (time.perf_counter() - started) * 1000,
                len(transcription),
            )
            if not transcription:
                raise RuntimeError("El servicio de voz devolvió una transcripción vacía.")

            # Queued signal: el chat actualiza la transcripción mientras este
            # hilo ya inicia la solicitud a OpenClaw.
            self.transcription_ready.emit(request_id, transcription)
            self._process_text_request(request_id, transcription, tts_enabled)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            self.request_failed.emit(request_id, str(exc))
