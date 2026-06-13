# Esquema de Seguridad — Plataforma de Pólizas

> **Contexto crítico:** este sistema procesa **datos personales de asegurados**
> (PII) para una **aseguradora**. El sector asegurador maneja información
> sensible y está sujeto a regulación (en Ecuador, la **Ley Orgánica de
> Protección de Datos Personales — LOPDP**). Por eso **la seguridad no es
> opcional ni un añadido tardío**: debe mantenerse un esquema robusto y
> verificable de forma continua. Este documento es la evidencia viva de esa
> postura y debe actualizarse en cada cambio que toque datos, autenticación,
> subida de archivos o dependencias.

Cubre los dos repositorios del sistema:

- **`polizas-web`** — Frontend React (Vite).
- **`polizas-api`** — Backend FastAPI (extracción de PDFs + generación de
  certificados Word).

---

## 1. Modelo de amenazas (resumen)

| Activo | Amenaza principal | Mitigación |
|---|---|---|
| PII de asegurados (datos extraídos, certificados, Word de referencia) | Acceso no autorizado / filtración | Autenticación por token, CORS restringido, HTTPS (despliegue) |
| Servidor de la API | Ejecución remota de código (RCE) | Sandbox de plantillas (SSTI), parser XML endurecido (XXE) |
| Sistema de archivos del servidor | Escritura/lectura fuera de ruta | Sanitización de nombres + validación de ruta contenida |
| Navegador del operador | XSS desde documento malicioso | Sanitización con DOMPurify del preview |
| Cadena de dependencias | Librerías con CVEs conocidas | Auditoría periódica (`npm audit`, `pip-audit`) |

**Supuesto de despliegue:** el frontend se embebe en un **WebView** del sistema
del dueño (la aseguradora). Ese host autentica al usuario e inyecta un token en
las llamadas. **El WebView por sí solo NO protege la API** — cualquiera que
alcance el host puede llamar los endpoints sin pasar por el WebView; por eso el
backend **valida el token en cada request**.

---

## 2. Cobertura del OWASP Top 10 (2021)

Leyenda: ✅ cubierto en código · 🟡 parcial / depende del despliegue · ⚪ N/A

| # | Categoría | Estado | Detalle |
|---|---|---|---|
| **A01** | Broken Access Control | 🟡 | Autenticación por token global implementada (`app/core/security.py`). Pendiente del lado del host: activar el token en producción y, si se requiere, separación de roles (autor de plantillas vs. procesador de datos). |
| **A02** | Cryptographic Failures | 🟡 | Token comparado en tiempo constante (`secrets.compare_digest`). **Pendiente (despliegue):** servir solo por HTTPS con HSTS y **cifrar la PII en reposo** (Word de referencia y datos extraídos). |
| **A03** | Injection | ✅ | SSTI cerrado (Jinja `SandboxedEnvironment` en docxtpl); path traversal cerrado (sanitización de `aseguradora`/`tipo_poliza`); XXE mitigado (parser lxml endurecido); **XSS** en el preview de Word cerrado con DOMPurify. |
| **A04** | Insecure Design | 🟡 | Modelo WebView + token validado en servidor es razonable. Pendiente: formalizar autorización por rol y política de retención de datos. |
| **A05** | Security Misconfiguration | ✅ | CORS restringido por configuración (sin wildcard); límites de tamaño en todas las subidas; parser XML sin entidades externas/DTD/red. |
| **A06** | Vulnerable & Outdated Components | ✅ | Auditoría ejecutada: frontend `npm audit` = 0; backend `pip-audit` reducido de 16 CVEs a 0. Ver §4. **Requiere repetición periódica.** |
| **A07** | Identification & Auth Failures | 🟡 | Token con comparación segura. Pendiente: política de token fuerte y rotación gestionada por el host. |
| **A08** | Software & Data Integrity Failures | ✅ | Sin deserialización insegura (no `pickle`/`yaml.load`/`eval`). |
| **A09** | Security Logging & Monitoring | 🟡 | Logging básico a stdout (registra **solo nombres de variables**, no valores PII). Con `STORAGE_BACKEND=mongo`, colección `auditoria` registra eventos de negocio (creación/edición/eliminación de plantillas, generación de certificados) — ver §3 y `docs/adr/0001-persistencia-mongodb.md`. Pendiente: identidad de usuario individual (hoy `usuario` queda `null`, solo hay token compartido) y envío de logs operacionales a CloudWatch en producción. |
| **A10** | SSRF | ⚪ | No hay peticiones a URLs controladas por el usuario. |

---

## 3. Controles implementados (referencia de código)

### Autenticación (A01/A07)
- `polizas-api/app/core/security.py` — `verificar_token` valida
  `Authorization: Bearer <API_TOKEN>` con `secrets.compare_digest`. Si
  `API_TOKEN` está vacío (dev) no exige nada; **en producción debe definirse**.
- Aplicada como dependencia global a los routers en `app/main.py`
  (`/health` y `/` quedan libres para health-checks).

### CORS (A05)
- `app/main.py` usa `settings.cors_origins_list` (configurable vía
  `CORS_ORIGINS`), nunca `*`.

### Path traversal (A03)
- `polizas-api/app/core/paths.py` — `slug()` reduce a `[a-z0-9_-]` y
  `ruta_template_docx()` valida que la ruta quede dentro de `templates_dir`.

### SSTI / RCE (A03)
- `polizas-api/app/services/generador.py` — render con
  `jinja2.sandbox.SandboxedEnvironment` (los `.docx` de plantilla los sube el
  usuario).

### XXE (A03/A05)
- Parser endurecido (`resolve_entities=False, no_network=True, load_dtd=False`)
  en `template_builder.py` y `generador.py`.

### XSS (A03)
- `polizas-web` — `EditarTemplatePage.jsx` y `NuevaPlantillaPage.jsx`
  sanitizan con `DOMPurify.sanitize()` el HTML de `mammoth` antes de
  `dangerouslySetInnerHTML`.

### Límites de subida (A05)
- `max_pdf_size_bytes` y `max_docx_size_bytes` (10 MB) → respuesta `413`.
- **Rate limiting** (anti-fuerza-bruta / ráfagas): se hace en el **proxy /
  API gateway** del host, no en la app (ver `.env.example`).

### Persistencia y auditoría (A09)
- `STORAGE_BACKEND` (`local` por defecto, `mongo` en producción) selecciona
  la implementación en `app/storage/__init__.py` — `local.py` (filesystem) o
  `mongo.py` (colección `plantillas` + GridFS para `.docx`). Ver
  `docs/adr/0001-persistencia-mongodb.md`.
- Colección `auditoria` (`app/storage/auditoria.py`): registra eventos de
  negocio (`plantilla_creada/editada/eliminada`, `certificado_generado`,
  `template_construido`) con `plantilla_id`, `aseguradora`, `tipo_poliza` y
  `timestamp` — **nunca valores de variables extraídas (PII)**. Sin TTL
  (retención por requisito de trazabilidad LOPDP). `registrar()` es
  best-effort: nunca bloquea la operación de negocio si Mongo falla.
- Credenciales de Mongo (`MONGO_URI`) van por variables de entorno /
  Secrets Manager — nunca en la imagen ni en git, igual que `API_TOKEN` y
  `ANTHROPIC_API_KEY`.

---

## 4. Gestión de dependencias (A06) — proceso obligatorio

Las CVEs aparecen de forma continua; un sistema de aseguradora **no puede**
quedarse con dependencias sin auditar. Ejecutar **al menos mensualmente** y
antes de cada despliegue:

```bash
# Frontend
npm audit

# Backend (desde polizas-api/)
venv/bin/pip-audit
```

Última auditoría: frontend **0 vulnerabilidades**; backend **0** tras
actualizar `fastapi`, `starlette`, `python-multipart`, `Pillow`,
`python-dotenv`, `pytest`/`pytest-asyncio`. Versiones congeladas en
`polizas-api/requirements.txt`. Tras cada actualización, correr la suite:

```bash
cd polizas-api && venv/bin/python -m pytest tests/ -q
```

---

## 5. Pendientes de despliegue (responsabilidad del host / aseguradora)

Estos controles **no viven en el código** de la POC y deben garantizarse en la
infraestructura productiva:

- [ ] **HTTPS obligatorio** con HSTS en el reverse proxy; no exponer la app
      directamente en `0.0.0.0:8000`.
- [ ] **`API_TOKEN` fuerte** definido en el entorno, con rotación.
- [ ] **Rate limiting** en nginx / API gateway (ej. `limit_req_zone`,
      `client_max_body_size`).
- [ ] **Cifrado de PII en reposo** (documentos de referencia y datos
      extraídos) + **política de retención/purga** conforme a la LOPDP. Con
      `STORAGE_BACKEND=mongo`, esto recae en la configuración de cifrado en
      reposo del cluster (ej. encryption-at-rest de MongoDB Atlas/Enterprise).
- [x] **Audit trail** de generación de certificados y cambios de plantilla:
      colección `auditoria` (ver §3, requiere `STORAGE_BACKEND=mongo`).
      Pendiente: identidad de usuario individual (hoy `usuario=null`).
- [ ] Separación de roles si hay múltiples tipos de usuario.
- [ ] Logs operacionales (stdout) → CloudWatch Logs en el contenedor (driver
      `awslogs`), con retención corta — distinto de la colección `auditoria`.

---

## 6. Reporte de vulnerabilidades

Ante el hallazgo de una vulnerabilidad, **no abrir un issue público**:
notificar de forma privada al responsable de seguridad del proyecto antes de
divulgar. Toda vulnerabilidad que afecte PII de asegurados debe tratarse como
**prioridad alta**.

---

_Última actualización: 2026-06-12. Mantener este documento al día es parte de
la definición de "hecho" de cualquier cambio con impacto en seguridad._
