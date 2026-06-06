# polizas-api

Backend FastAPI para la POC de generación de certificados individuales de pólizas de seguros.

## Estado POC
- ✅ Extracción OCR de PDFs escaneados (Generali Ecuador)
- ✅ CRUD de plantillas de extracción (almacenamiento JSON local)
- ✅ Generación de certificados .docx con marcadores
- ⏸️ MongoDB (pendiente MVP)
- ⏸️ Autenticación (pendiente MVP)

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

## Preparar el template .docx

1. Abre `FONDO_DE_JUBILACION_DE_LA_CONTRALORIA__pdf_resultado_.docx` en Word
2. Reemplaza los valores variables por marcadores `{{nombre_variable}}`
3. Usa los mismos nombres que definas en las cajas de la plantilla
4. Guarda como `templates/generali_desgravamen.docx`

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
```

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
│       └── local.py         # JSON local (reemplazar por MongoDB en MVP)
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

- Sin autenticación (MVP)
- Sin cifrado de archivos (MVP)
- Sin auditoría de accesos (MVP)
- Sin cumplimiento LOPDP para datos reales (hardening)
- Un solo tipo de póliza por aseguradora en el template .docx
