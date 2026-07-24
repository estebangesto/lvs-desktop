import sys
import os
import json
import threading
import time
import logging
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
from PIL import Image
import pystray
from pynput import keyboard
from pygame import mixer

# GUI Imports
from PySide6.QtWidgets import (QApplication, QMainWindow, QLineEdit, QHBoxLayout, 
                               QWidget, QPushButton, QVBoxLayout, QScrollArea, 
                               QLabel, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QColor
import io
from ws_client import VoiceApiClient

# --- CONFIGURACIÓN ---
TRIGGER_KEY_VK = 183
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
TEMP_IN = "mic_capture.wav"
TEMP_OUT = "lucia_reply.mp3"

# --- LOGGING ---
logger = logging.getLogger("lucia-desktop")
logger.setLevel(logging.DEBUG)
logger.propagate = False
file_handler = logging.FileHandler("lucia-desktop.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
file_handler.setLevel(logging.WARNING)
logger.addHandler(file_handler)


def configure_debug_logging(enabled: bool) -> None:
    """Ajusta el detalle del log sin dejar de registrar advertencias y errores."""
    file_handler.setLevel(logging.DEBUG if enabled else logging.WARNING)

class VoiceSignals(QObject):
    update_icon = Signal(str)
    request_exit = Signal()
    show_window = Signal()
    append_chat = Signal(str, str) # sender, text
    clear_chat = Signal() # Signal for thread-safe clearing

class LuciaDesktopLink(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config_file = os.path.join(os.path.dirname(__file__), "ldl_config.json")
        self.load_config()

        self.signals = VoiceSignals()
        self.signals.update_icon.connect(self.set_tray_state)
        self.signals.request_exit.connect(self.cleanup_and_exit)
        self.signals.show_window.connect(self.toggle_window_state)
        self.signals.append_chat.connect(self.add_message_to_chat)
        self.signals.clear_chat.connect(self.on_clear_chat)
        
        self.recording = False
        self.processing = False
        self.speaking = False
        self.key_pressed = False
        self.cancelled = False
        self.ignore_until_release = False
        self.debug_enabled = self.config.get("debug_enabled", False)
        self.tts_enabled = self.config.get("tts_enabled", True)
        configure_debug_logging(self.debug_enabled)
        logger.debug("Inicio de Luc.ia Desktop (debug=%s, tts=%s)", self.debug_enabled, self.tts_enabled)
        self.audio_data = []
        self.fs = 16000

        # --- INICIALIZACIÓN DE UI Y RECURSOS ---
        try:
            self.icons = {
                "standby": Image.open(os.path.join(ASSETS_DIR, "standby.png")),
                "recording": Image.open(os.path.join(ASSETS_DIR, "recording.png")),
                "thinking": Image.open(os.path.join(ASSETS_DIR, "thinking.png")),
                "speaking": Image.open(os.path.join(ASSETS_DIR, "speaking.png")),
            }
        except Exception as e:
            logger.error(f"Error cargando iconos: {e}")
            img = Image.new('RGB', (64, 64), color = (73, 109, 137))
            self.icons = {"standby": img, "recording": img, "thinking": img, "speaking": img}
        
        try:
            mixer.init()
        except Exception as e:
            logger.error(f"Error Mixer: {e}")

        self.setup_ui()
        self.setup_tray()
        self.start_keyboard_listener()

        api_url = self.config.get("server_url", "http://127.0.0.1:8001")
        self.voice_client = VoiceApiClient(
            url=api_url,
            timeout_seconds=self.config.get("network_timeout", 150),
        )
        self.voice_client.request_succeeded.connect(self.on_api_success)
        self.voice_client.request_failed.connect(self.on_api_error)
        self.voice_client.transcription_ready.connect(self.on_transcription_ready)
        self.voice_client.health_changed.connect(self.on_api_health)
        self.voice_client.check_health()
        self.active_request_id = None
        self.active_request_type = None
        self.play_timer = QTimer()
        self.play_timer.timeout.connect(self.check_playback)
        self.play_timer.start(200)

        # Variables para redimensión de ventana (Paso 6)
        self._is_resizing = False
        self._resize_edge = None
        self._resize_margin = 15 # Margen en píxeles para detectar el borde
        self.setMouseTracking(True) # Para cambiar el cursor
        self.centralWidget().setMouseTracking(True)

    def load_config(self):
        self.default_config = {
            "server_url": "http://127.0.0.1:8001",
            "tts_enabled": True,
            "debug_enabled": False,
            "window_width": 450,
            "window_height": 500,
            "network_timeout": 150,
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                # Merge with defaults to ensure all keys exist
                for k, v in self.default_config.items():
                    if k not in self.config:
                        self.config[k] = v
            except Exception as e:
                logger.error(f"Error cargando config: {e}")
                self.config = self.default_config.copy()
        else:
            self.config = self.default_config.copy()
            self.save_config()

    def save_config(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            logger.error(f"Error guardando config: {e}")

    def update_window_style(self, state):
        # ... (rest of method unchanged)
        colors = {
            "standby": "#808080",   # Gris (Reposo)
            "recording": "#ff3333", # Rojo (Grabando)
            "thinking": "#0078d7",  # Azul (Pensando)
            "speaking": "#33cc33"   # Verde (Hablando)
        }
        color = colors.get(state, "#808080")
        
        if self.has_bg:
            style = f"""
                QWidget#MainFrame {{
                    border-image: url({self.bg_path}) 0 0 0 0 stretch stretch;
                    border: 2px solid {color};
                    border-radius: 15px;
                }}
            """
        else:
            style = f"""
                QWidget#MainFrame {{
                    background-color: rgba(35, 35, 35, 250);
                    border: 2px solid {color};
                    border-radius: 15px;
                }}
            """
        # Asegurarnos de que el widget central exista antes de aplicar
        if self.centralWidget():
            self.centralWidget().setStyleSheet(style)

    def setup_ui(self):
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(self.config["window_width"], self.config["window_height"])
        
        # Configuración de fondo (se usa en update_window_style)
        self.bg_path = None
        self.has_bg = False
        
        # Buscar imagen en orden de preferencia: PNG > JPG > JPEG
        for ext in [".png", ".jpg", ".jpeg"]:
            p = os.path.join(ASSETS_DIR, f"background{ext}")
            if os.path.exists(p):
                self.bg_path = p.replace("\\", "/")
                self.has_bg = True
                break
        
        central_widget = QWidget()
        central_widget.setObjectName("MainFrame")
        self.setCentralWidget(central_widget)
        self.update_window_style("standby") # Estilo inicial
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        # --- Chat Area (QScrollArea with Widgets) ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff) # Sin barra horizontal
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { width: 8px; background: transparent; }
            QScrollBar::handle:vertical { background: #555; border-radius: 4px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
        
        self.chat_content = QWidget()
        self.chat_content.setStyleSheet("background: transparent; border: none;") # Asegurar sin bordes
        self.chat_layout = QVBoxLayout(self.chat_content)
        self.chat_layout.setContentsMargins(5, 5, 5, 10) # Padding extra abajo
        self.chat_layout.setSpacing(15) 
        self.chat_layout.addStretch()
        
        self.scroll_area.setWidget(self.chat_content)
        main_layout.addWidget(self.scroll_area)
        
        # --- Separador ---
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #555;")
        main_layout.addWidget(line)
        
        # --- Input Area ---
        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)

        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Escribí algo...")
        self.text_input.setStyleSheet("background: transparent; color: white; border: none; font-size: 15px;")
        self.text_input.returnPressed.connect(self.on_text_submitted)
        self.text_input.textChanged.connect(self.on_input_typing) # Conectar señal de escritura
        input_layout.addWidget(self.text_input)
        
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #888; font-size: 18px; border: none; }
            QPushButton:hover { color: #fff; }
        """)
        self.close_btn.clicked.connect(self.hide_window)
        input_layout.addWidget(self.close_btn)
        
        main_layout.addLayout(input_layout)
        
        self.center_on_tray()
        self.hide()

    def add_message_to_chat(self, sender, text):
        # Remove the bottom stretch if it's the first message or move it to end?
        # Better approach: Insert before the last item if it's a stretch, but QVBoxLayout works linearly.
        # Let's just append. If we want messages to start from top, we don't use initial stretch at top.
        # If we want them bottom-aligned like WhatsApp, we use stretch at TOP.
        # Let's remove the stretch from __init__ and put it at index 0 always.
        
        # Check if we have the initial stretch item at pos 0
        if self.chat_layout.count() > 0:
            item = self.chat_layout.itemAt(0)
            if item.spacerItem():
                pass # Keep it there to push content down
            else:
                self.chat_layout.insertStretch(0)
        else:
            self.chat_layout.addStretch()

        # Create message container
        msg_container = QWidget()
        msg_container.setStyleSheet("background: transparent;")
        msg_layout = QVBoxLayout(msg_container)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        msg_layout.setSpacing(4) # Separación nombre-burbuja
        
        # Name Label
        name_label = QLabel()
        name_font = QFont("Segoe UI", 8)
        name_font.setBold(True)
        name_label.setFont(name_font)
        
        # Bubble Label
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setFont(QFont("Segoe UI", 10))
        # Habilitar selección de texto con el mouse
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # Limitar ancho máximo de burbuja
        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)

        if sender == "user":
            name_label.setText("Esteban")
            name_label.setStyleSheet("color: #00BFFF; margin-bottom: 2px;")
            name_label.setAlignment(Qt.AlignRight)
            
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #2078CF;
                    color: white;
                    border: 0px solid #5a8bb5;
                    border-radius: 12px;
                    padding: 10px;
                }
            """)
            
            # Layout: Stretch - Container(Name + Bubble)
            msg_layout.addWidget(name_label)
            msg_layout.addWidget(bubble)
            
            row_layout.addStretch(20) # 20% espacio a la izquierda
            row_layout.addWidget(msg_container, 80) # 80% ancho mensaje
            row_layout.addSpacing(10) # Pequeño margen derecho
            
        else:
            name_label.setText("Luc.ia ✨")
            name_label.setStyleSheet("color: #FFD700; margin-bottom: 2px;")
            name_label.setAlignment(Qt.AlignLeft)
            
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #461A8C;
                    color: white;
                    border: 0px solid #384652;
                    border-radius: 12px;
                    padding: 10px;
                }
            """)
            
            msg_layout.addWidget(name_label)
            msg_layout.addWidget(bubble)
            
            row_layout.addSpacing(10) # Pequeño margen izquierdo
            row_layout.addWidget(msg_container, 80) # 80% ancho mensaje
            row_layout.addStretch(20) # 20% espacio a la derecha

        # Wrap row in a widget to add to main vertical layout
        row_widget = QWidget()
        row_widget.setLayout(row_layout)
        self.chat_layout.addWidget(row_widget)
        
        # Scroll to bottom
        QTimer.singleShot(50, self.scroll_to_bottom)

    def scroll_to_bottom(self):
        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_clear_chat(self):
        # Delete all items in layout
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.chat_layout.addStretch()

    def hide_window(self):
        self.text_input.clear()
        self.hide()
        if not self.processing and not self.speaking:
            self.signals.update_icon.emit("standby")

    def toggle_window_state(self):
        if self.isVisible():
            self.hide_window()
        else:
            self.show_input()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide_window()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edge = self._get_resize_edge(event.pos())
            if edge:
                self._is_resizing = True
                self._resize_edge = edge
                self._mouse_press_pos = event.globalPos()
                self._window_start_rect = self.geometry()
                event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_resizing:
            delta = event.globalPos() - self._mouse_press_pos
            rect = self._window_start_rect

            # Implementamos redimensión desde la derecha, abajo y esquina inferior-derecha.
            # Como la app está anclada a la derecha de la pantalla (normalmente), 
            # estirar la izquierda o arriba mueve la ventana y cambia el tamaño.
            # Por simplicidad, habilitaremos Right, Bottom y BottomRight.
            new_width = rect.width()
            new_height = rect.height()
            new_x = rect.x()
            new_y = rect.y()

            if "right" in self._resize_edge:
                new_width = max(300, rect.width() + delta.x())
            if "bottom" in self._resize_edge:
                new_height = max(400, rect.height() + delta.y())
            if "left" in self._resize_edge:
                new_width = max(300, rect.width() - delta.x())
                if new_width > 300:
                    new_x = rect.x() + delta.x()
            if "top" in self._resize_edge:
                new_height = max(400, rect.height() - delta.y())
                if new_height > 400:
                    new_y = rect.y() + delta.y()

            self.setGeometry(new_x, new_y, new_width, new_height)
            event.accept()
        else:
            edge = self._get_resize_edge(event.pos())
            if edge in ["top-left", "bottom-right"]:
                self.setCursor(Qt.SizeFDiagCursor)
            elif edge in ["top-right", "bottom-left"]:
                self.setCursor(Qt.SizeBDiagCursor)
            elif edge in ["left", "right"]:
                self.setCursor(Qt.SizeHorCursor)
            elif edge in ["top", "bottom"]:
                self.setCursor(Qt.SizeVerCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_resizing = False
            self._resize_edge = None
            self.unsetCursor()
        super().mouseReleaseEvent(event)

    def _get_resize_edge(self, pos):
        # Determinar si el mouse está cerca de los bordes
        x = pos.x()
        y = pos.y()
        w = self.width()
        h = self.height()
        m = self._resize_margin

        left = x < m
        right = x > w - m
        top = y < m
        bottom = y > h - m

        if top and left: return "top-left"
        if top and right: return "top-right"
        if bottom and left: return "bottom-left"
        if bottom and right: return "bottom-right"
        if left: return "left"
        if right: return "right"
        if top: return "top"
        if bottom: return "bottom"
        return None

    def center_on_tray(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.width() - self.width() - 20
        y = screen.height() - self.height() - 15 
        self.move(x, y)

    def setup_tray(self):
        self.tray_menu = pystray.Menu(
            pystray.MenuItem("Mostrar/Ocultar", self.on_show_clicked, default=True),
            pystray.MenuItem("Silenciar Voz (TTS)", self.toggle_tts, checked=lambda item: not self.tts_enabled),
            pystray.MenuItem("Modo Debug", self.toggle_debug, checked=lambda item: self.debug_enabled),
            pystray.MenuItem("Limpiar Chat", lambda: self.signals.clear_chat.emit()),
            pystray.MenuItem("Abrir Logs", lambda: os.startfile("lucia-desktop.log")),
            pystray.MenuItem("Editar Configuración", lambda: os.startfile(self.config_file)),
            pystray.MenuItem("Salir", self.on_exit_clicked)
        )
        self.tray_icon = pystray.Icon("Lucia", self.icons["standby"], "Luc.ia Desktop", self.tray_menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def toggle_debug(self, icon, item):
        self.debug_enabled = not self.debug_enabled
        self.config["debug_enabled"] = self.debug_enabled
        self.save_config()
        if self.debug_enabled:
            configure_debug_logging(True)
            logger.debug("Modo Debug activado")
        else:
            logger.debug("Modo Debug desactivado")
            configure_debug_logging(False)

    def toggle_tts(self, icon, item):
        self.tts_enabled = not self.tts_enabled
        self.config["tts_enabled"] = self.tts_enabled
        self.save_config()
        logger.debug("Modo TTS: %s", "ACTIVADO" if self.tts_enabled else "DESACTIVADO")

    def set_tray_state(self, state):
        if state in self.icons:
            self.tray_icon.icon = self.icons[state]
        self.update_window_style(state)

    def on_show_clicked(self, icon, item):
        if self.speaking:
            self.stop_audio()
        else:
            self.signals.show_window.emit()

    def show_input(self):
        self.signals.update_icon.emit("standby")
        self.center_on_tray()
        self.show()
        self.raise_()
        self.activateWindow()
        self.text_input.setFocus()
        QTimer.singleShot(100, self.scroll_to_bottom)

    def on_input_typing(self, text):
        # Si hay texto y no estamos en otro estado activo, mostramos "grabando/activo" (Rojo)
        # Si borra todo, volvemos a standby (Gris)
        if not self.processing and not self.speaking and not self.recording:
            if text.strip():
                # Actualizar tanto la ventana (Rojo) como el ícono (Molty Recording)
                self.signals.update_icon.emit("recording")
            else:
                self.signals.update_icon.emit("standby")

    def on_text_submitted(self):
        text = self.text_input.text().strip()
        if text and not self.processing:
            self.signals.append_chat.emit("user", text)
            self.text_input.clear() 
            QTimer.singleShot(100, self.scroll_to_bottom)
            self.request_start_time = time.time()
            self.request_type = "TEXT"
            self.processing = True
            self.active_request_type = "TEXT"
            self.signals.update_icon.emit("thinking")
            self.active_request_id = self.voice_client.send_text(text)

    def start_keyboard_listener(self):
        self.key_listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        self.key_listener.start()

    def on_press(self, key):
        try:
            if key == keyboard.Key.esc and self.recording:
                self.cancel_recording()
                return
            vk = getattr(key, 'vk', None)
            if vk == TRIGGER_KEY_VK:
                if self.processing:
                    return
                if self.speaking:
                    self.stop_audio()
                if not self.key_pressed:
                    self.key_pressed = True
                    self.cancelled = False
                    self.start_voice_recording()
        except: pass

    def on_release(self, key):
        try:
            vk = getattr(key, 'vk', None)
            if vk == TRIGGER_KEY_VK:
                self.key_pressed = False
                if self.recording and not self.cancelled:
                    self.stop_voice_recording()
        except: pass

    def start_voice_recording(self):
        self.signals.update_icon.emit("recording")
        self.recording = True
        self.audio_data = []
        def callback(indata, frames, time, status):
            if self.recording: self.audio_data.append(indata.copy())
        self.stream = sd.InputStream(samplerate=self.fs, channels=1, callback=callback)
        self.stream.start()
        # No mostramos la ventana automáticamente al grabar (petición de usuario)
        # if self.isHidden():
        #    self.signals.show_window.emit()

    def cancel_recording(self):
        self.recording = False
        self.cancelled = True
        if hasattr(self, 'stream'):
            self.stream.stop()
            self.stream.close()
        self.signals.update_icon.emit("standby")

    def stop_voice_recording(self):
        if self.recording:
            self.recording = False
            self.processing = True
            self.signals.update_icon.emit("thinking")
            if hasattr(self, 'stream'):
                self.stream.stop()
                self.stream.close()
            if len(self.audio_data) > 0:
                full_audio = np.concatenate(self.audio_data, axis=0)
                wav_io = io.BytesIO()
                wav.write(wav_io, self.fs, full_audio)
                wav_bytes = wav_io.getvalue()
                self.request_start_time = time.time()
                self.request_type = "VOICE"
                self.active_request_type = "VOICE"
                self.active_request_id = self.voice_client.send_voice(wav_bytes, self.tts_enabled)
            else:
                self.processing = False
                self.signals.update_icon.emit("standby")

    def stop_audio(self):
        if mixer.get_init():
            mixer.music.stop()
            mixer.music.unload()
        self.speaking = False
        self.signals.update_icon.emit("standby")

    def on_api_health(self, healthy, message):
        logger.debug("[API] Healthcheck: healthy=%s", healthy)
        if not healthy:
            self.signals.append_chat.emit("lucia", message)

    def on_transcription_ready(self, request_id, text):
        if self.active_request_id not in (None, request_id):
            return
        self.active_request_id = request_id
        self.signals.append_chat.emit("user", text)
        QTimer.singleShot(50, self.scroll_to_bottom)

    def on_api_success(self, request_id, result):
        if self.active_request_id not in (None, request_id):
            logger.debug("[API] Respuesta obsoleta ignorada: request_id=%s", request_id)
            return
        self.active_request_id = request_id

        request_type = self.active_request_type
        if result.get("response_text"):
            self.signals.append_chat.emit("lucia", result["response_text"])

        total_ms = result.get("_client_total_ms")
        if total_ms is not None:
            logger.debug("[TIMING %s] E2E: %.2fs", request_type, total_ms / 1000)

        self.processing = False
        self.active_request_id = None
        self.active_request_type = None
        audio_bytes = result.get("_audio_bytes")
        if audio_bytes and self.tts_enabled:
            self.play_audio(audio_bytes)
        else:
            self.signals.update_icon.emit("standby")

    def on_api_error(self, request_id, error):
        if self.active_request_id not in (None, request_id):
            return
        self.active_request_id = request_id
        logger.error("[API] %s", error)
        self.signals.append_chat.emit("lucia", f"Error de comunicación: {error}")
        self.processing = False
        self.active_request_id = None
        self.active_request_type = None
        self.signals.update_icon.emit("standby")

    def play_audio(self, audio_bytes):
        try:
            if not mixer.get_init():
                return
            mixer.music.stop()
            mixer.music.unload()
            with open(TEMP_OUT, "wb") as file:
                file.write(audio_bytes)
            mixer.music.load(TEMP_OUT)
            mixer.music.play()
            self.speaking = True
            self.signals.update_icon.emit("speaking")
        except Exception as exc:
            logger.error("Error de reproducción: %s", exc)
            self.signals.append_chat.emit("lucia", "Recibí la respuesta, pero no pude reproducir el audio.")
            self.speaking = False
            self.signals.update_icon.emit("standby")

    def check_playback(self):
        if self.speaking and mixer.get_init() and not mixer.music.get_busy():
            self.stop_audio()

    def on_exit_clicked(self, icon, item):
        self.signals.request_exit.emit()

    def cleanup_and_exit(self):
        if hasattr(self, 'tray_icon'): self.tray_icon.stop()
        if hasattr(self, 'key_listener'): self.key_listener.stop()
        if mixer.get_init(): mixer.quit()
        os._exit(0)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = LuciaDesktopLink()
    sys.exit(app.exec())
