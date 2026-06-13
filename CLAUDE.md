# CLAUDE.md — polizas-api

Guía para Claude Code al trabajar en este repositorio.

## Qué es esto

Backend **FastAPI** (Python 3.11) de una POC para una **aseguradora**: extrae
datos de PDFs de pólizas (Generali) y genera certificados individuales en Word.
Trabaja en pareja con el frontend **`polizas-web`** (React/Vite), en
`/Users/belen/Documents/GitHub/polizas-web` — muchos cambios tocan ambos repos.

## Comandos

```bash
# SIEMPRE usar el venv (Python 3.11). El python3.9 del sistema rompe numpy/pandas.
venv/bin/python -m pytest                # tests + cobertura (config en pytest.ini)
venv/bin/pip-audit                       # auditoría de dependencias (OWASP A06)
venv/bin/uvicorn app.main:app --reload   # servidor dev en :8000
```

**Cobertura de tests:** `pytest.ini` mide cobertura de `app/` y **falla si baja
del 80%** (`--cov-fail-under=80`). Cobertura actual ~88%. Al agregar código
nuevo, agregar sus tests para no romper la puerta de calidad. Fixtures
compartidas (client, PDFs, .docx, plantilla de prueba) en `tests/conftest.py`.
Las llamadas a la API de Claude se **mockean** (ver `tests/test_extractor_llm.py`).

- El servidor uvicorn **se reinicia manualmente** para tomar cambios de código.
- Config por entorno en `.env` (ver `.env.example`). `VITE_API_URL` del frontend
  apunta aquí (default `http://localhost:8000`).

## Arquitectura (pipeline)

1. **Plantilla** (`data/plantillas.json`): variables a extraer (`variables` con
   nombre + descripción para la IA), `campos_manuales` y opcionalmente `cajas`
   (coordenadas, modo antiguo). CRUD en `app/routers/plantillas.py`.
2. **Extracción** (`POST /polizas/upload`), tres estrategias en orden:
   - **IA** (`app/services/extractor_llm.py`): si hay `ANTHROPIC_API_KEY`, manda
     el texto del PDF a Claude con las variables de la plantilla.
   - **Capa de texto directa** (`app/services/extractor.py`): recorta por cajas.
   - **OCR Tesseract**: fallback para PDFs escaneados.
3. **Generación** (`POST /certificados/generar`): docxtpl rellena el template
   `templates/{aseguradora}_{tipo_poliza}.docx` (`app/services/generador.py`).
4. **Template builder** (`app/services/template_builder.py`): construye el
   template Word a partir de un Word de referencia con datos reales.

`Variable.origen`: `extraido` (OCR), `extraido_directo`, `extraido_ia`, `manual`.

## Convenciones clave

- Los nombres de variable conectan tres lugares y deben coincidir: `plantillas.json`
  ↔ marcador `{{variable}}` en el template Word ↔ valor extraído.
- Prefijo `listado_` en una variable: la limpieza separa el texto en un párrafo
  por ítem; sin prefijo se une todo en un párrafo.
- Los templates de salida (`templates/*.docx`) y los `.env` **no están en git**.
- Errores de dominio en `app/core/errors.py`; config en `app/core/config.py`.

## Seguridad (NO negociable — PII de asegurados, LOPDP)

El detalle y la cobertura OWASP Top 10 están en **`SECURITY.md`**. Controles ya
implementados que **no debes romper**:

- **Auth** (`app/core/security.py`): `verificar_token` valida
  `Authorization: Bearer <API_TOKEN>` con `secrets.compare_digest`, aplicado
  global en `app/main.py`. Vacío en dev = sin auth; en producción el host
  (WebView) inyecta el token y el backend lo valida server-side.
- **CORS** por `CORS_ORIGINS`, nunca `*`.
- **Path traversal**: todo nombre de archivo derivado de input del usuario pasa
  por `app/core/paths.py` (`slug()` + validación de ruta contenida). No
  construyas rutas concatenando `aseguradora`/`tipo_poliza` a mano.
- **SSTI**: `generador.py` renderiza docxtpl con `SandboxedEnvironment` (los
  `.docx` los sube el usuario → no confiable).
- **XXE**: parser lxml endurecido en `template_builder.py` y `generador.py`.
- **Límites de subida**: `max_pdf_size_bytes` / `max_docx_size_bytes` → 413.
- **Deps (A06)**: `venv/bin/pip-audit` mensual y pre-deploy; tras actualizar,
  correr `pytest`.

**Reglas al editar:**

1. Input de usuario → ruta de archivo: sanitiza y valida contención en el dir.
2. Contenido subido (`.docx`/PDF) es no confiable: valida tamaño, trátalo en
   entornos endurecidos (sandbox Jinja, parser XML seguro).
3. **Nunca** loguear valores PII (nombres, cédulas, montos) — solo nombres de
   variables / identificadores.
4. Toda validación/autorización de seguridad ocurre aquí (backend), no en el
   cliente.

## Buenas prácticas para "vibe coding"

- **Verifica con tests.** Todo cambio se valida con `pytest tests/ -q` antes de
  darlo por hecho; nunca afirmes que algo funciona sin evidencia.
- **Reutiliza patrones** existentes (`app/core/`, helpers de routers) antes de
  crear nuevos; consistencia > novedad.
- **Un cambio, un propósito.** No mezcles arreglos no relacionados.
- **Pregunta las decisiones de negocio** (límites, modelo de auth) — no inventes
  defaults silenciosos en algo que afecta seguridad.
- **Repasa la sección de Seguridad** al tocar subidas, rutas, render o auth.
- **Mantén `SECURITY.md` y este archivo vivos** en el mismo cambio.
- **No comitees secretos** (`.env`, `ANTHROPIC_API_KEY`, templates con datos reales).
