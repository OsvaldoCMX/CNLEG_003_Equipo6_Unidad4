"""Fixtures compartidas. Cada prueba recibe datos NUEVOS: ninguna depende de otra."""
import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.services import build_service
from cargar_csv import load_fixture


@pytest.fixture
def orders():
    """Los 16 pedidos del CSV, recién cargados (todos en DRAFT)."""
    return load_fixture()


@pytest.fixture
def client():
    """Cliente HTTP con el estado de la API reiniciado."""
    app.state.service = build_service()
    with TestClient(app) as c:
        yield c
