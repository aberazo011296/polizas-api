# polizas-api

Backend FastAPI para la POC de generación de certificados individuales de pólizas de seguros.

## Estado POC
- ✅ Extracción OCR/IA de PDFs (Generali Ecuador)
- ✅ CRUD de plantillas de extracción
- ✅ Generación de certificados .docx con marcadores
- ✅ Autenticación por token (`API_TOKEN`)
- ✅ Persistencia en MongoDB (opcional, `STORAGE_BACKEND=mongo`) + auditoría —
  ver [docs/adr/0001-persistencia-mongodb.md](docs/adr/0001-persistencia-mongodb.md)

## Requisitos previos

- Python 3.12+
- Tesseract OCR instalado en el sistema

### Instalar Tesseract

**Ubuntu/Debian:**
```bash
sudo apt-get install tesseract-ocr tesseract-ocr-spa
```

**macOS:**
```bash
brew install tesseract
brew install tesseract-lang  # Para idioma español
```

## Setup local

```bash
# Clonar e instalar dependencias
cd polizas-api
pip install -r requirements.txt

# Configurar entorno
cp .env.example .env

# Ejecutar
uvicorn app.main:app --reload
```

Documentación interactiva: http://localhost:8000/docs

## Persistencia: MongoDB local (opcional)

Por defecto (`STORAGE_BACKEND=local`) no se necesita Mongo: las plantillas
viven en `data/plantillas.json` y los `.docx` en filesystem.

Para usar MongoDB (recomendado antes de desplegar en contenedores, ver el ADR):

```bash
# 1. Levantar solo Mongo (no la API) con Docker
docker compose up -d mongo

# 2. En .env:
#    STORAGE_BACKEND=mongo
#    MONGO_URI=mongodb://polizas:polizas_dev_pw@localhost:27017
#    MONGO_DB_NAME=polizas

# 3. Migrar las plantillas/templates existentes (una sola vez)
venv/bin/python -m scripts.migrar_a_mongo

# 4. Reiniciar uvicorn
```

Con `STORAGE_BACKEND=mongo` también se activa la colección `auditoria`
(eventos de negocio: plantillas creadas/editadas, certificados generados).
Consultar el historial de una plantilla: `GET /plantillas/{id}/auditoria`.

**Réplica en contenedor (AWS):** la app no cambia — solo `MONGO_URI` pasa a
apuntar a un Mongo gestionado (Atlas o Amazon DocumentDB) en vez de
`localhost`, inyectado vía Secrets Manager/SSM. `docker compose --profile full
up --build` corre la API en el mismo contenedor que se sube a ECS/Fargate,
contra el Mongo local, para probar esa configuración antes de desplegar.

## Preparar el template .docx

El template es un archivo Word con marcadores `{{nombre_variable}}` donde deben aparecer los datos extraídos del PDF. Los nombres deben coincidir exactamente con los definidos en las cajas de la plantilla.

1. Abre el documento Word base en Word o LibreOffice
2. Reemplaza los valores variables por marcadores `{{nombre_variable}}`
3. Usa los mismos nombres que definiste en las cajas de la plantilla
4. Sube el archivo desde la interfaz web al crear la plantilla (paso 4 del wizard), o manualmente via API: `POST /plantillas/{id}/template`

El archivo se guarda automáticamente como `templates/{aseguradora}_{tipo_poliza}.docx`.

**Marcadores estándar para Generali Desgravamen:**
```
{{numero_poliza}}       → número de póliza
{{contratante}}         → nombre del contratante
{{ruc}}                 → RUC (ingreso manual — no aparece en el PDF)
{{nombre_asegurado}}    → nombre del asegurado
{{cedula}}              → cédula del asegurado
{{fecha_nacimiento}}    → fecha de nacimiento
{{vigencia_desde}}      → inicio de vigencia
{{vigencia_hasta}}      → fin de vigencia
{{numero_certificado}}  → número de certificado individual
{{fecha_emision}}       → fecha de emisión del certificado
```

## Flujo de uso

### Primera vez (crear plantilla)

```bash
# 1. Subir PDF modelo para ver dimensiones
curl -X POST http://localhost:8000/polizas/upload/sin-plantilla \
  -F "archivo=@poliza_ejemplo.pdf"

# 2. Crear plantilla con las cajas (coordenadas en pixels del canvas)
curl -X POST http://localhost:8000/plantillas \
  -H "Content-Type: application/json" \
  -d '{
    "nombre": "Generali Desgravamen 2026",
    "aseguradora": "generali",
    "tipo_poliza": "desgravamen",
    "cajas": [
      {"nombre": "numero_poliza", "pagina": 0, "x": 120, "y": 180, "ancho": 180, "alto": 28},
      {"nombre": "contratante", "pagina": 1, "x": 50, "y": 180, "ancho": 500, "alto": 25}
    ]
  }'

# 3. Subir el template .docx para esa plantilla
curl -X POST http://localhost:8000/plantillas/<ID_DE_PLANTILLA>/template \
  -F "archivo=@mi_template.docx"
# El archivo se guarda automáticamente como templates/generali_desgravamen.docx
```

> **Nota:** También puedes hacer esto desde la interfaz web — el wizard de "Nueva plantilla" incluye el paso de subir el `.docx` al final.

### Uso normal (generar certificado)

```bash
# 1. Subir nueva póliza y extraer campos
curl -X POST http://localhost:8000/polizas/upload \
  -F "archivo=@nueva_poliza.pdf" \
  -F "plantilla_id=<ID_DE_PLANTILLA>"

# 2. Revisar y completar variables, luego generar
curl -X POST http://localhost:8000/certificados/generar \
  -H "Content-Type: application/json" \
  -d '{
    "plantilla_id": "<ID_DE_PLANTILLA>",
    "variables": {
      "numero_poliza": "990664",
      "contratante": "FONDO DE JUBILACION...",
      "ruc": "1791746112001",
      "nombre_asegurado": "Juan Pérez",
      "cedula": "1234567890",
      "vigencia_desde": "01/Feb/2026",
      "vigencia_hasta": "01/Feb/2027"
    }
  }' --output certificado.docx
```

## Tests

```bash
pytest tests/ -v
```

## Estructura del proyecto

```
polizas-api/
├── app/
│   ├── main.py              # FastAPI app, CORS, routers
│   ├── core/
│   │   ├── config.py        # Settings (pydantic-settings)
│   │   └── errors.py        # Excepciones del dominio
│   ├── models/
│   │   ├── plantilla.py     # Plantilla, Caja, Variable, ResultadoExtraccion
│   │   └── certificado.py   # CertificadoRequest/Response
│   ├── routers/
│   │   ├── polizas.py       # POST /polizas/upload
│   │   ├── plantillas.py    # CRUD /plantillas
│   │   └── certificados.py  # POST /certificados/generar
│   ├── services/
│   │   ├── extractor.py     # OCR con PyMuPDF + Tesseract
│   │   └── generador.py     # Relleno de template .docx
│   └── storage/
│       ├── __init__.py      # Selector local/mongo según STORAGE_BACKEND
│       ├── local.py         # JSON + filesystem (default)
│       ├── mongo.py         # Colección `plantillas` + GridFS
│       ├── db.py            # Cliente MongoDB compartido
│       └── auditoria.py     # Colección `auditoria` (eventos de negocio)
├── templates/               # Archivos .docx de salida por aseguradora
├── data/                    # plantillas.json (gitignored)
├── uploads/                 # PDFs temporales (gitignored)
└── tests/
```

## Nota sobre PDFs escaneados

Los PDFs de Generali Ecuador son imágenes escaneadas (sin capa de texto).
La extracción usa OCR (Tesseract) sobre las regiones marcadas en la plantilla.
La calidad del OCR depende de la resolución del scan — los PDFs de Generali
funcionan correctamente a 200 DPI de renderizado.

## Limitaciones de la POC

- Sin cifrado de archivos en reposo (depende de la config del cluster Mongo en producción)
- Auditoría sin identidad de usuario individual (solo token compartido — `usuario` queda `null`)
- Un solo tipo de póliza por aseguradora en el template .docx
