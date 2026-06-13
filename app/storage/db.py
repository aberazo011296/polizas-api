"""
Cliente compartido de MongoDB. Usado por storage/mongo.py y storage/auditoria.py
cuando STORAGE_BACKEND=mongo. Ver docs/adr/0001-persistencia-mongodb.md.
"""
from functools import lru_cache

from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import settings


@lru_cache
def get_client() -> MongoClient:
    return MongoClient(settings.mongo_uri)


def get_db() -> Database:
    return get_client()[settings.mongo_db_name]
