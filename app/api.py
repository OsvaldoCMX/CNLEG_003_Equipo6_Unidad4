"""
API REST de TechStore (FastAPI).

Los controladores son "delgados": reciben la petición, llaman al Application
Service y convierten el resultado a un DTO. No validan reglas de negocio ni
cambian el status; eso lo decide Order. Las excepciones de dominio se
traducen a códigos HTTP en un solo lugar (ver HTTP_STATUS_BY_ERROR).

Ejecutar:  uvicorn app.api:app --reload   ->   http://127.0.0.1:8000/docs
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain import DomainException, InvalidOrderData, InvalidOrderTransition, OrderItemLimitExceeded
from app.schemas import (
    AddItemRequest,
    CancelRequest,
    CreateOrderRequest,
    DiscountDTO,
    ErrorDTO,
    EventDTO,
    OrderDTO,
    OrderSummaryDTO,
    ReturnRequest,
    ShipRequest,
)
from app.services import ApplicationError, NotFound, OrderService, build_service

DESCRIPTION = """
Núcleo de negocio de **TechStore** (CLN · Unidad 4 · FIME UANL).

* Datos en memoria cargados de `datos/*.csv` (16 pedidos, todos en `DRAFT`).
* Máquina de estados: `DRAFT → CONFIRMED → SHIPPED → DELIVERED`,
  `CONFIRMED → CANCELLED`, `SHIPPED → RETURNED`.
* Errores de dominio → HTTP: **404** no existe · **409** transición inválida ·
  **422** regla de negocio/datos inválidos.
* Cada error responde `{"error": "<código>", "mensaje": "<texto para el cliente>"}`;
  el mensaje está en español sencillo y no muestra códigos internos de estado.
* Usa `POST /admin/reset` para volver al estado inicial entre pruebas.
"""

app = FastAPI(
    title="TechStore U4 – Núcleo de negocio",
    version="1.0.0",
    description=DESCRIPTION,
    openapi_tags=[
        {"name": "Pedidos", "description": "Consulta y armado de pedidos"},
        {"name": "Ciclo de vida", "description": "Transiciones de la máquina de estados"},
        {"name": "Descuentos", "description": "Evaluación con Specification Pattern"},
        {"name": "Eventos", "description": "Domain Events publicados y notificaciones"},
        {"name": "Utilidades", "description": "Apoyo para pruebas"},
    ],
)
app.state.service = build_service()


def get_service(request: Request) -> OrderService:
    return request.app.state.service


# ---------------------------------------------------------------------------
# Excepciones de dominio -> códigos HTTP semánticos
# ---------------------------------------------------------------------------
HTTP_STATUS_BY_ERROR: dict[type, int] = {
    NotFound: 404,                # el recurso no existe
    InvalidOrderTransition: 409,  # conflicto con el estado actual del pedido
    OrderItemLimitExceeded: 422,  # regla de negocio violada
    InvalidOrderData: 422,        # invariante violada (vacío, sin guía, inactivo...)
}


@app.exception_handler(DomainException)
@app.exception_handler(ApplicationError)
async def business_error_handler(request: Request, exc: Exception) -> JSONResponse:
    code = next(
        (HTTP_STATUS_BY_ERROR[c] for c in type(exc).__mro__ if c in HTTP_STATUS_BY_ERROR),
        400,  # cualquier otra regla de dominio no mapeada
    )
    return JSONResponse(status_code=code, content={"error": type(exc).__name__, "mensaje": str(exc)})


def _explain_validation_error(err: dict) -> str:
    """Traduce un error de validación de FastAPI/Pydantic a una frase sencilla."""
    names = [str(x) for x in err.get("loc", ()) if isinstance(x, str) and x not in ("body", "path", "query")]
    field = names[-1] if names else None
    kind = err.get("type", "")
    if kind == "json_invalid":
        return "los datos enviados no tienen un formato JSON válido"
    if field is None:
        return "faltan los datos de la petición"
    if kind == "missing":
        return f"falta el dato '{field}'"
    if kind.startswith("int"):
        return f"'{field}' debe ser un número entero"
    if kind.startswith("bool"):
        return f"'{field}' debe ser verdadero (true) o falso (false)"
    if kind.startswith("string"):
        return f"'{field}' debe ser texto"
    return f"el dato '{field}' no es válido"


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = list(dict.fromkeys(_explain_validation_error(e) for e in exc.errors()))
    return JSONResponse(
        status_code=422,
        content={"error": "DatosInvalidos", "mensaje": "Revisa la información enviada: " + "; ".join(problems) + "."},
    )


E404 = {404: {"model": ErrorDTO, "description": "El pedido, cliente o producto no existe"}}
E409 = {409: {"model": ErrorDTO, "description": "Transición no permitida en el estado actual"}}
E422 = {422: {"model": ErrorDTO, "description": "Se viola una regla de negocio (datos inválidos)"}}
ORDER_ID = Path(description="Id del pedido", examples=["O001"])


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------
@app.get("/orders", response_model=list[OrderSummaryDTO], tags=["Pedidos"], summary="Listar pedidos")
def list_orders(service: OrderService = Depends(get_service)):
    return [OrderSummaryDTO.from_domain(o) for o in service.list()]


@app.get("/orders/{order_id}", response_model=OrderDTO, tags=["Pedidos"], summary="Consultar un pedido",
         responses=E404)
def get_order(order_id: str = ORDER_ID, service: OrderService = Depends(get_service)):
    return OrderDTO.from_domain(service.get(order_id))


@app.post("/orders", response_model=OrderDTO, status_code=201, tags=["Pedidos"],
          summary="Crear pedido vacío (pendiente de confirmar)", responses=E404)
def create_order(body: CreateOrderRequest, service: OrderService = Depends(get_service)):
    return OrderDTO.from_domain(service.create(body.customer_id, body.has_promotion))


@app.post("/orders/{order_id}/items", response_model=OrderDTO, tags=["Pedidos"],
          summary="Agregar artículo (máx. 20 unidades por pedido)", responses={**E404, **E409, **E422})
def add_item(body: AddItemRequest, order_id: str = ORDER_ID, service: OrderService = Depends(get_service)):
    return OrderDTO.from_domain(service.add_item(order_id, body.sku, body.quantity))


# ---------------------------------------------------------------------------
# Ciclo de vida (máquina de estados)
# ---------------------------------------------------------------------------
@app.post("/orders/{order_id}/confirm", response_model=OrderDTO, tags=["Ciclo de vida"],
          summary="Confirmar pedido",
          description="Transición técnica: DRAFT → CONFIRMED. Requiere artículos y cliente activo.", responses={**E404, **E409, **E422})
def confirm_order(order_id: str = ORDER_ID, service: OrderService = Depends(get_service)):
    return OrderDTO.from_domain(service.confirm(order_id))


@app.post("/orders/{order_id}/ship", response_model=OrderDTO, tags=["Ciclo de vida"],
          summary="Enviar pedido (requiere número de guía)",
          description="Transición técnica: CONFIRMED → SHIPPED.", responses={**E404, **E409, **E422})
def ship_order(body: ShipRequest | None = None, order_id: str = ORDER_ID,
               service: OrderService = Depends(get_service)):
    return OrderDTO.from_domain(service.ship(order_id, body.tracking_number if body else None))


@app.post("/orders/{order_id}/cancel", response_model=OrderDTO, tags=["Ciclo de vida"],
          summary="Cancelar pedido (requiere motivo)",
          description="Transición técnica: CONFIRMED → CANCELLED.", responses={**E404, **E409, **E422})
def cancel_order(body: CancelRequest | None = None, order_id: str = ORDER_ID,
                 service: OrderService = Depends(get_service)):
    return OrderDTO.from_domain(service.cancel(order_id, body.reason if body else None))


@app.post("/orders/{order_id}/deliver", response_model=OrderDTO, tags=["Ciclo de vida"],
          summary="Marcar pedido como entregado",
          description="Transición técnica: SHIPPED → DELIVERED.", responses={**E404, **E409})
def deliver_order(order_id: str = ORDER_ID, service: OrderService = Depends(get_service)):
    return OrderDTO.from_domain(service.deliver(order_id))


@app.post("/orders/{order_id}/return", response_model=OrderDTO, tags=["Ciclo de vida"],
          summary="Registrar devolución del pedido",
          description="Transición técnica: SHIPPED → RETURNED.", responses={**E404, **E409})
def return_order(body: ReturnRequest | None = None, order_id: str = ORDER_ID,
                 service: OrderService = Depends(get_service)):
    return OrderDTO.from_domain(service.return_order(order_id, body.reason if body else None))


# ---------------------------------------------------------------------------
# Descuentos, eventos y utilidades
# ---------------------------------------------------------------------------
@app.get("/orders/{order_id}/discount", response_model=DiscountDTO, tags=["Descuentos"],
         summary="Evaluar descuento (10 % VIP / 5 % monto alto)", responses=E404)
def evaluate_discount(order_id: str = ORDER_ID, service: OrderService = Depends(get_service)):
    return DiscountDTO.from_domain(service.evaluate_discount(order_id))


@app.get("/events", response_model=list[EventDTO], tags=["Eventos"], summary="Domain Events publicados")
def published_events(service: OrderService = Depends(get_service)):
    return [EventDTO.from_domain(e) for e in service.bus.published]


@app.get("/notifications", response_model=list[str], tags=["Eventos"],
         summary="Correos simulados enviados por el suscriptor EmailNotifier")
def notifications(service: OrderService = Depends(get_service)):
    return list(service.notifier.outbox) if service.notifier else []


@app.post("/admin/reset", tags=["Utilidades"], summary="Recargar los 16 pedidos del CSV (estado inicial)")
def reset(request: Request):
    request.app.state.service = build_service()
    return {"status": "ok", "orders": len(request.app.state.service.list())}
