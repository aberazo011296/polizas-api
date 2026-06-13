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

## Persistencia (`app/storage/`)

`STORAGE_BACKEND` (`.env`) selecciona el backend vía `app/storage/__init__.py`
— los routers/servicios importan SIEMPRE de `app.storage`, nunca de
`storage.local`/`storage.mongo` directo:

- `local` (default, sin Mongo): `data/plantillas.json` + `.docx` en filesystem
  (`app/storage/local.py`).
- `mongo`: colección `plantillas` + `.docx` en GridFS (`app/storage/mongo.py`),
  más colección `auditoria` (`app/storage/auditoria.py`) con eventos de negocio
  (creación/edición/eliminación de plantillas, certificados generados — sin PII,
  sin TTL). Requiere `MONGO_URI`/`MONGO_DB_NAME`.

Para desarrollo local con Mongo: `docker compose up -d mongo` (ver
`docker-compose.yml` y `.env.example`). Migración de datos existentes:
`venv/bin/python -m scripts.migrar_a_mongo`. Detalle de la decisión y plan de
despliegue en `docs/adr/0001-persistencia-mongodb.md`.

## Arquitectura (pipeline)

1. **Plantilla**: variables a extraer (`variables` con nombre + descripción
   para la IA), `campos_manuales` y opcionalmente `cajas` (coordenadas, modo
   antiguo). CRUD en `app/routers/plantillas.py`, persistida vía `app.storage`.
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

### Coberturas como grupo repetible (lista dinámica)

Una plantilla puede declarar `coberturas_campos: list[VariableDef]` (sub-campos
de UNA cobertura: `nombre`, `suma_asegurada`, `descripcion`,
`listado_exclusiones`, `listado_limites_edad`, `listado_docs_siniestro`…). Si no
está vacío, la IA devuelve una **lista** de coberturas (una por fila del cuadro
de coberturas de la póliza — 2, 3 o N) en `ResultadoExtraccion.coberturas`. La
convención `listado_` aplica a los sub-campos igual que a las variables planas.
`ProcesarPage` muestra una tarjeta editable por cobertura; la generación pasa
`coberturas` al contexto docxtpl para recorrerlas con un loop.

**Sintaxis del Word de salida (docxtpl) — gotcha importante:**
- En **párrafos**: `{%p for c in coberturas %}` … `{%p endfor %}` (la `p` borra
  el párrafo de control para no dejar líneas vacías).
- En **tablas**: `{%tr for c in coberturas %}` y `{%tr endfor %}` van en **filas
  separadas** (cada tag reemplaza su fila entera), con la fila de contenido
  `{{c.nombre}}`/`{{c.suma_asegurada}}` **en medio**. Ponerlos en la misma fila
  rompe con "Encountered unknown tag 'endfor'".
- Dentro del loop se usa `{{c.<subcampo>}}`; los `\n` de los `listado_` se
  expanden a párrafos vía `_br_a_parrafos`.

**Creación de plantilla:** "Sugerir desde un PDF" (`POST /plantillas/sugerir-variables`,
`sugerir_variables`) devuelve `{variables, coberturas_campos}`: separa los datos a
nivel póliza de los sub-campos de cobertura, y propone el grupo repetible solo si
detecta un cuadro de coberturas (si no, `coberturas_campos: []`). El wizard
(`NuevaPlantillaPage`) tiene una sección **opcional** "Coberturas que se repiten"
para definir/editar esos sub-campos. Es opcional: una plantilla sin
`coberturas_campos` se comporta como antes (todo campo plano).
**Pendiente:** el template builder (`/template/build`) aún no inserta los loops
`{%p/%tr for%}` en el `.docx` automáticamente — para una plantilla dinámica nueva,
los loops del Word se agregan a mano (ver más arriba).

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
