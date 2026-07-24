# Luc.ia Desktop

Cliente Windows de Luc.ia para interacción de texto y voz sin cambiar de ventana.

## Alcance

- Chat de texto contra `lucia-voice-service`.
- Voz mediante la tecla Calculadora (`VK_183`): mantener para grabar y soltar para enviar.
- La conversación por voz muestra la transcripción y la respuesta en el chat.
- TTS opcional para solicitudes de voz; los mensajes escritos nunca solicitan audio.
- Bandeja del sistema, ventana sin bordes, estados visuales y reproducción local.

Los adjuntos y el drag & drop no forman parte del alcance actual.

## API esperada

`POST {server_url}/voice/process`

- Texto: formulario con `text`; la API devuelve solo `response_text`.
- Voz: primero `POST /voice/transcribe` para mostrar la transcripción; luego
  `POST /voice/process` con ese texto y `tts=true|false`. La respuesta puede
  incluir `response_audio_b64`.

La URL por defecto es `http://127.0.0.1:8001`.

## Configuración

En el primer inicio se crea `ldl_config.json`:

```json
{
  "server_url": "http://127.0.0.1:8001",
  "tts_enabled": true,
  "debug_enabled": false,
  "window_width": 450,
  "window_height": 500,
  "network_timeout": 150
}
```

## Logs

El menú de bandeja **Modo Debug** controla el detalle de `lucia-desktop.log` y se
guarda en `debug_enabled`:

- Desactivado (valor inicial): conserva solo advertencias y errores.
- Activado: agrega diagnóstico técnico de solicitudes, estados y tiempos de
  STT/API/TTS, sin registrar transcripciones, respuestas, Base64 ni secretos.

## Dependencias

Creá un entorno virtual local y activalo antes de instalar las dependencias:

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Estados

- Gris: disponible.
- Rojo: grabando o escribiendo.
- Azul: solicitud en proceso.
- Verde: reproduciendo respuesta.

Solo se procesa una solicitud a la vez. Las respuestas de solicitudes obsoletas
se ignoran para evitar que reproduzcan audio o alteren el chat actual.
