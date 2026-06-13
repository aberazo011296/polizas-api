# ADR-0001: Migrar la persistencia de `plantillas.json` (filesystem) a MongoDB y agregar auditoría

**Status:** Proposed
**Date:** 2026-06-12
**Deciders:** Ana Erazo (dueña del producto), equipo de la aseguradora (define infra de MongoDB y AWS)

## Context

Hoy todo el estado persistente de `polizas-api` vive en el filesystem local del proceso:

- `data/plantillas.json`: diccionario `{id_plantilla: Plantilla}` con `variables`,
  `campos_manuales`, `cajas`, `reemplazos_template` y `doc_referencia`. Leído/escrito
  completo en cada operación (`app/storage/local.py`).
- `templates/{aseguradora}_{tipo_poliza}.docx`: templates Word de salida que
  `docxtpl` rellena para generar certificados.
- `data/referencias/*`: Word de referencia subido al construir un template
  (`template_builder.py`).

Esto es suficiente para una POC en una sola máquina, pero es incompatible con
un despliegue en contenedores en AWS (ECS/Fargate, App Runner, EKS):

1. **Filesystem efímero**: cada redeploy, reinicio o autoescalado borra
   `plantillas.json` y los `.docx` generados/subidos — se pierde todo el
   trabajo de configuración de plantillas.
2. **Múltiples réplicas**: si corre más de una instancia del contenedor, cada
   una tiene su propia copia de `plantillas.json` → inconsistencia.
3. **Escrituras no atómicas**: `_escribir_plantillas` reescribe el archivo
   completo sin lock; con concurrencia real hay riesgo de corrupción/pérdida
   de escrituras.

Además, **la aseguradora ya opera MongoDB** como base de datos corporativa, lo
cual fija la tecnología de destino (no es una elección libre entre
Postgres/Mongo/etc. — es alinearse con la infraestructura que el equipo de
plataforma del cliente va a operar, respaldar y monitorear).

El propio código ya anticipa esto — `app/storage/local.py` dice explícitamente:

> "En el MVP esto se reemplaza por MongoDB — la interfaz (save/get/list/delete)
> no cambia, solo la implementación."

Este ADR define **cómo** hacer ese reemplazo y qué hacer con los archivos
binarios (`.docx`), que MongoDB no maneja igual de bien que datos estructurados.

Contexto de seguridad relevante (`SECURITY.md`, `CLAUDE.md`): el sistema
maneja **PII de asegurados** (LOPDP, Ecuador). `plantillas.json` en sí no
contiene PII (son definiciones de extracción), pero la migración debe dejar
el camino preparado para que datos con PII (futuro: histórico de
certificados generados, si se decide persistirlos) se guarden cifrados en
reposo y sin exponerse en logs.

Adicionalmente, hoy no existe **ningún registro de auditoría** de quién
crea/edita/elimina plantillas o genera certificados — solo logs de proceso
(`logging` a stdout en `app/main.py`, sin persistencia). Para un sistema con
PII regulado por LOPDP, la trazabilidad de "qué se hizo, cuándo y con qué
plantilla" es un requisito de negocio, no solo técnico. Esta migración es el
momento natural para resolverlo, porque ya se está introduciendo Mongo como
almacén transaccional.

## Decision

1. Migrar la persistencia de plantillas de `data/plantillas.json` a una
   colección **`plantillas`** en MongoDB, manteniendo intacta la interfaz
   actual de `app/storage/local.py` (`guardar_plantilla`, `obtener_plantilla`,
   `listar_plantillas`, `actualizar_plantilla`, `eliminar_plantilla`) detrás de
   un módulo nuevo `app/storage/mongo.py`, seleccionado por configuración.

2. Agregar una colección **`auditoria`** en el mismo Mongo para registrar
   eventos de negocio (auditoría, no logs operacionales de la app — ver
   "Alcance de logging" más abajo).

Para los archivos binarios (`templates/*.docx` y `data/referencias/*.docx`):
usar **GridFS de MongoDB** en lugar de S3, para no introducir una segunda
pieza de infraestructura (y por ende un segundo modelo de permisos/backup) en
una integración donde Mongo ya es el almacén "oficial" del cliente. Se
documenta S3 como alternativa por si la aseguradora ya tiene ese bucket
provisionado y prefiere reservar Mongo solo para datos estructurados.

Driver: **PyMongo** (síncrono) — todos los endpoints de `plantillas.py` son
`def` (no `async def`), salvo los dos que llaman a la IA. Migrar a Motor
(async) no aporta nada hoy y añade complejidad; si en el futuro se requiere
alta concurrencia real, se puede revisar.

## Alcance de logging: auditoría de negocio (sí) vs logs operacionales (no, por ahora)

Es importante distinguir dos cosas que suelen confundirse bajo "logs":

- **Auditoría de negocio** (lo que cubre esta decisión): eventos discretos y
  de bajo volumen como "se creó la plantilla X", "se generó un certificado
  para la plantilla Y", "se editó el mapeo de variables de Z". Esto es valor
  de negocio/cumplimiento (LOPDP) y tiene sentido vivir junto a los datos
  transaccionales en Mongo, con retención larga (años).
- **Logs operacionales** (fuera de alcance de este ADR): el `logging` actual
  a stdout (`app/main.py`) — trazas de requests, errores de extracción IA,
  warnings de OCR. Volumen alto, vida útil corta (días/semanas), y en un
  despliegue en contenedores en AWS su destino natural es **CloudWatch Logs**
  (stdout del contenedor → driver `awslogs`), no la base transaccional.
  Meterlos en Mongo obligaría a TTL indexes y crecería la colección sin
  aportar al negocio. Se deja explícitamente fuera; se puede revisar en el
  ADR de despliegue si CloudWatch no es viable.

### Diseño de la colección `auditoria`

Documento por evento, sin PII de asegurados (igual que las reglas ya vigentes
para logs en `SECURITY.md`):

```json
{
  "_id": ObjectId,
  "timestamp": ISODate,
  "evento": "plantilla_creada | plantilla_editada | plantilla_eliminada | certificado_generado | template_construido",
  "plantilla_id": "uuid",
  "aseguradora": "generali",
  "tipo_poliza": "desgravamen",
  "usuario": "string | null",
  "detalle": { "campos_modificados": ["..."], "num_variables": 12 }
}
```

- `usuario`: hoy la API solo valida un token compartido (`API_TOKEN`), no
  identifica usuarios individuales — el campo queda `null` hasta que exista
  esa identidad (fuera de alcance aquí, pero el esquema ya la contempla).
- `detalle` es un objeto libre por tipo de evento — nunca incluye valores de
  variables extraídas (esos sí pueden ser PII).
- Índices: `{ "plantilla_id": 1, "timestamp": -1 }` y `{ "evento": 1,
  "timestamp": -1 }` para las consultas típicas ("historial de esta
  plantilla", "todos los certificados generados en un rango").
- Sin TTL: a diferencia de logs operacionales, la auditoría se conserva
  (requisito de trazabilidad LOPDP).

### Puntos de instrumentación

En los mismos handlers que ya escriben a `app/storage/local.py` /
`mongo.py` (`app/routers/plantillas.py` y `app/routers/certificados.py`),
agregar una llamada a un helper `app/storage/auditoria.py::registrar(evento,
**campos)` justo después de la operación exitosa — no antes, para no auditar
intentos fallidos como éxitos. Mismo patrón de "interfaz estable" que
`storage/local.py`: la implementación real (insert a Mongo) se aísla en un
módulo propio.

## Options Considered

### Option A: MongoDB para todo (colección `plantillas` + GridFS para `.docx`)

| Dimension | Assessment |
|-----------|------------|
| Complejidad | Media — un solo sistema externo, pero GridFS añade una capa extra sobre PyMongo |
| Costo | Bajo si el cluster Mongo del cliente ya existe y tiene espacio |
| Escalabilidad | Suficiente para el volumen actual (decenas de plantillas, .docx de pocos MB, límite ya validado en 10MB) |
| Familiaridad del equipo del cliente | Alta — ya operan Mongo |
| Alineación con infraestructura existente | Alta |

**Pros:**
- Una sola dependencia externa nueva (Mongo), que el cliente ya sabe operar,
  respaldar y monitorear.
- `app/storage/local.py` ya está diseñado para este swap — bajo riesgo,
  cambio acotado.
- GridFS resuelve el problema de filesystem efímero para los `.docx` sin
  pedirle al cliente que provisione y dé permisos sobre un bucket S3 nuevo.

**Cons:**
- GridFS no es ideal para servir archivos vía HTTP directamente (hay que leer
  el stream y devolverlo); para el volumen actual (decenas de descargas, no
  miles) esto es irrelevante.
- Si el cluster Mongo del cliente tiene cuotas de almacenamiento ajustadas,
  los binarios compiten por ese espacio con los datos transaccionales.

### Option B: MongoDB para `plantillas` (colección) + S3 para `.docx`

| Dimension | Assessment |
|-----------|------------|
| Complejidad | Media-alta — dos sistemas externos, dos modelos de credenciales/IAM |
| Costo | Bajo (S3 es barato), pero suma una pieza más a aprovisionar |
| Escalabilidad | Mejor para servir archivos grandes/muchas descargas (no aplica aquí) |
| Familiaridad del equipo del cliente | Depende — puede que no tengan bucket dedicado |
| Alineación con infraestructura existente | Media — añade un sistema fuera del "Mongo es nuestra DB" |

**Pros:**
- S3 es el patrón estándar para binarios en AWS; URLs pre-firmadas, ciclo de
  vida, versionado nativo.
- Separa claramente "datos" (Mongo) de "archivos" (S3) — más fácil de
  escalar si el volumen de `.docx` crece mucho.

**Cons:**
- Requiere que la aseguradora aprovisione un bucket, política IAM para el
  contenedor, y que el equipo defina quién lo administra — coordinación
  adicional fuera del alcance de "ya tienen Mongo".
- Dos sistemas que pueden quedar desincronizados (plantilla referencia un
  `doc_referencia` que ya no está en S3, o viceversa).

### Option C: Mantener filesystem + volumen persistente (EFS)

| Dimension | Assessment |
|-----------|------------|
| Complejidad | Baja a corto plazo, alta a largo (no resuelve concurrencia) |
| Costo | EFS es más caro que S3/Mongo para este volumen |
| Escalabilidad | Pobre — sigue siendo lectura/escritura de un JSON completo |
| Alineación con infraestructura existente | Baja — el cliente pidió Mongo |

**Pros:** cambio mínimo de código.

**Cons:** no resuelve la escritura no atómica del JSON, no aprovecha la
infraestructura Mongo del cliente, EFS añade latencia y costo. Se descarta.

## Trade-off Analysis

La decisión central es **Option A vs B**, y se reduce a una pregunta que no
es técnica sino organizacional: **¿la aseguradora ya tiene (o está dispuesta
a provisionar fácilmente) un bucket S3 con permisos para este servicio?**

- Si **no** (caso más probable dado que el requisito explícito fue "usamos
  MongoDB"), **Option A (GridFS)** evita bloquear el proyecto en un
  aprovisionamiento adicional y mantiene un solo punto de backup/DR (el
  cluster Mongo, que presumiblemente ya tiene política de respaldo).
- Si **sí**, Option B es más "AWS-idiomático" y deja Mongo libre para crecer
  solo en datos estructurados — pero es una optimización prematura para el
  volumen actual (un puñado de templates de pocos MB cada uno).

Dado que el volumen de archivos es bajo y estable (un `.docx` por
`aseguradora_tipo_poliza`, más Word de referencia ocasionales, todos bajo el
límite de 10MB ya impuesto), **GridFS es suficiente y reduce superficie de
infraestructura**. Si el cliente confirma que ya tiene S3 listo para este
proyecto, Option B es un cambio acotado al módulo de storage de archivos —
no afecta la decisión sobre la colección `plantillas`.

## Consequences

- **Más fácil:**
  - Redeploys, reinicios y múltiples réplicas dejan de perder configuración —
    requisito bloqueante para cualquier despliegue en contenedores serio.
  - `app/storage/local.py` ya define la interfaz; el cambio es
    principalmente escribir `app/storage/mongo.py` con la misma firma y
    cambiar el import/factory.
  - Las migraciones de esquema (ej. el campo `reemplazos_template` que se
    agregó después) se vuelven más controlables con un esquema de versión de
    documento en lugar de "el JSON ya tiene o no ese campo".

- **Más difícil / a revisar:**
  - Hay que añadir **PyMongo** a `requirements.txt` y un cliente de conexión
    en `app/core/config.py` (`mongo_uri`, `mongo_db_name`), con manejo de
    error si Mongo no está disponible al arrancar.
  - Los tests actuales (`tests/conftest.py`, fixtures con `plantilla_creada`)
    asumen el JSON local; necesitan una alternativa — `mongomock` para tests
    unitarios rápidos, y opcionalmente un Mongo real (Testcontainers o
    `docker-compose` de CI) para tests de integración del storage real.
  - Migración de datos: escribir un script puntual que lea
    `data/plantillas.json` (17 plantillas hoy) y los `.docx` existentes en
    `templates/` y `data/referencias/`, e inserte en Mongo/GridFS. Correr una
    sola vez antes del primer deploy a producción.
  - `SECURITY.md` y `CLAUDE.md` deben actualizarse: nueva dependencia externa,
    credenciales de Mongo van por Secrets Manager/SSM (igual que
    `API_TOKEN`/`ANTHROPIC_API_KEY`, nunca en la imagen ni en git), y dejar
    constancia de que `plantillas.json`/`templates/*.docx` locales quedan
    como fallback de desarrollo o se eliminan del repo de producción.
  - El `Dockerfile` actual no cambia (sigue siendo solo la app); se necesita
    un `docker-compose.yml` de desarrollo con un contenedor Mongo local para
    no depender de un cluster remoto en cada `npm run dev` / pytest local.
  - Decidir el ID: mantener los UUID de `plantilla_id` como `_id` (string) en
    Mongo evita romper referencias existentes en el frontend
    (`4fb14239-cb2a-443d-b4a8-cd0d78fb252b` para "Generali1").

## Action Items

1. [ ] Confirmar con el equipo de la aseguradora: connection string/credenciales
   de un cluster Mongo de desarrollo, y si existe un bucket S3 ya disponible
   (para descartar definitivamente Option B vs A).
2. [ ] Agregar `pymongo` a `requirements.txt`; `mongo_uri`/`mongo_db_name` a
   `app/core/config.py` (vía `.env`, igual patrón que el resto de settings).
3. [ ] Implementar `app/storage/mongo.py` con la misma interfaz de
   `app/storage/local.py` (`guardar_plantilla`, `obtener_plantilla`,
   `listar_plantillas`, `actualizar_plantilla`, `eliminar_plantilla`) +
   helpers de GridFS para `.docx` (subir/descargar por `plantilla_id`).
4. [ ] Factory/selector de storage por config (`storage_backend: "local" |
   "mongo"`) para no romper el flujo de desarrollo/tests existente durante la
   transición.
5. [ ] Script de migración one-shot: `data/plantillas.json` + `templates/*.docx`
   + `data/referencias/*` → Mongo/GridFS.
6. [ ] Adaptar `tests/conftest.py` para usar `mongomock` (o Mongo real vía
   Testcontainers) en lugar de JSON en disco; mantener cobertura ≥80%.
7. [ ] `docker-compose.yml` de desarrollo: app + Mongo local.
8. [ ] Crear colección `auditoria` + `app/storage/auditoria.py::registrar(...)`
   con el esquema descrito arriba, e instrumentar los handlers de
   `plantillas.py` (crear/actualizar/eliminar/construir template) y
   `certificados.py` (generar) para llamarlo tras cada operación exitosa.
9. [ ] Agregar tests de `auditoria.py` (incluyendo que NO se registre nada en
   operaciones fallidas) y un endpoint de solo-lectura para consultar el
   historial de una plantilla (`GET /plantillas/{id}/auditoria`), si el
   frontend lo necesita.
10. [ ] Actualizar `SECURITY.md` (nueva dependencia externa, manejo de
    credenciales Mongo, cifrado en tránsito/reposo, y la colección `auditoria`
    como control de trazabilidad LOPDP) y `CLAUDE.md` (arquitectura de
    persistencia) en el mismo cambio.
11. [ ] Solo después de lo anterior: abordar el ADR de despliegue en
    contenedores en AWS — ya con un backend stateless, ese diseño es mucho más
    simple (sin volúmenes, sin coordinación de réplicas para `plantillas.json`).
    Los logs operacionales (stdout) se resuelven ahí vía CloudWatch, fuera del
    alcance de este ADR.
