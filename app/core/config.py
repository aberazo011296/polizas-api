from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    uploads_dir: Path = Path("./uploads")
    data_dir: Path = Path("./data")
    templates_dir: Path = Path("./templates")

    tesseract_cmd: str | None = None

    # Extracción automática con IA (si no hay key, se usan las cajas)
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    # Tamaño máximo de PDF en bytes (10 MB)
    max_pdf_size_bytes: int = 10 * 1024 * 1024

    # Tamaño máximo de template/documento .docx en bytes (10 MB)
    max_docx_size_bytes: int = 10 * 1024 * 1024

    # Token de acceso a la API. Lo provee el sistema del dueño (la app que
    # embebe el frontend en un WebView lo inyecta como `Authorization: Bearer`).
    # Si queda vacío (dev), la API NO exige token. En producción debe definirse.
    api_token: str | None = None

    # Orígenes permitidos para CORS (coma-separados en el .env).
    # Default: localhost del frontend en dev. En producción, el origin del host.
    cors_origins: str = "http://localhost:5173,http://localhost:5199"

    # Backend de persistencia: "local" (plantillas.json + filesystem, default)
    # o "mongo" (colección `plantillas` + GridFS + colección `auditoria`).
    # Ver docs/adr/0001-persistencia-mongodb.md.
    storage_backend: str = "local"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "polizas"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # El mismo .env lo lee docker-compose (MONGO_INITDB_ROOT_* son solo
        # para inicializar el contenedor de Mongo, no los usa la app).
        "extra": "ignore",
    }

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def model_post_init(self, __context):
        # Crear directorios si no existen
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
