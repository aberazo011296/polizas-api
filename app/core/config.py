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

    # Tamaño máximo de PDF en bytes (10 MB)
    max_pdf_size_bytes: int = 10 * 1024 * 1024

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    def model_post_init(self, __context):
        # Crear directorios si no existen
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
