"""
Autenticación de la API.

Modelo: el frontend se embebe en un WebView del sistema del dueño (la
aseguradora). Ese host autentica al usuario e inyecta un token en las
llamadas (`Authorization: Bearer <token>`). La API valida ese token en
cada request — el WebView por sí solo NO protege la API (cualquiera que
alcance el host puede llamar los endpoints sin pasar por el WebView).

Si `settings.api_token` está vacío (entorno de desarrollo), la validación
se desactiva para no estorbar el flujo local. En producción debe definirse
`API_TOKEN` en el `.env`.
"""
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def verificar_token(authorization: str | None = Header(default=None)) -> None:
    # Sin token configurado → modo dev, no se exige autenticación.
    if not settings.api_token:
        return

    esperado = f"Bearer {settings.api_token}"
    # Comparación en tiempo constante para no filtrar el token por timing.
    if not authorization or not secrets.compare_digest(authorization, esperado):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autorizado",
            headers={"WWW-Authenticate": "Bearer"},
        )
