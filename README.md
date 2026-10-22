# LVS Desktop

Cliente Windows de LVS para interacción de texto y voz sin cambiar de ventana.

## Alcance

- Chat de texto contra `lvs-gateway`.
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

En el primer inicio se crea `lvs_config.json`:

```json
{
  "server_url": "http://127.0.0.1:8001",
  "tts_enabled": true,
  "debug_enabled": false,
  "window_width": 450,
  "window_height": 500,
  "network_timeout": 150,
  "user_name": "User",
  "assistant_name": "Assistant",
  "window_title": "LVS Desktop"
}
```

## Logs

El menú de bandeja **Modo Debug** controla el detalle de `lvs-desktop.log` y se
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

python -m pip install "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
python -m pip install -r requirements.txt
```

Se requiere `certifi>=2026.10.19` para usar el paquete de autoridades de
certificación corregido ante el incidente de confianza emSign/eMudhra de octubre
de 2026. En entornos existentes, actualizá pip y las dependencias del proyecto
con los comandos anteriores.

## Estados

- Gris: disponible.
- Rojo: grabando o escribiendo.
- Azul: solicitud en proceso.
- Verde: reproduciendo respuesta.

Solo se procesa una solicitud a la vez. Las respuestas de solicitudes obsoletas
se ignoran para evitar que reproduzcan audio o alteren el chat actual.
