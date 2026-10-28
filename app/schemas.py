"""
DTOs (Data Transfer Objects) de la API.

Son la frontera HTTP: describen lo que entra y sale en JSON y alimentan la
documentación OpenAPI (/docs). No contienen reglas de negocio; por ejemplo,
ShipRequest acepta una guía vacía para que sea el DOMINIO quien la rechace.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain import DomainEvent, Money, Order, OrderItem
from app.specifications import DiscountDecision


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class CreateOrderRequest(BaseModel):
    customer_id: str = Field(description="Id del cliente en clientes.csv", examples=["C001"])
    has_promotion: bool = Field(False, description="¿El pedido tiene una promoción activa?")


class AddItemRequest(BaseModel):
    sku: str = Field(description="SKU de productos.csv", examples=["SKU001"])
    quantity: int = Field(description="Unidades a agregar (el dominio exige > 0)", examples=[2])


class ShipRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"tracking_number": "TRK-0001"}]})
    tracking_number: str | None = Field(None, description="Número de guía (obligatorio para el dominio)")


class CancelRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"reason": "El cliente cambió de opinión"}]})
    reason: str | None = Field(None, description="Motivo de cancelación (obligatorio para el dominio)")


class ReturnRequest(BaseModel):
    reason: str | None = Field(None, description="Motivo de la devolución (opcional)")


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
def _plain(value: Any) -> Any:
    if isinstance(value, Money):
        return f"{value.amount:.2f} {value.currency}"
    if isinstance(value, datetime):
        return value.isoformat()
    return value


EVENT_DESCRIPTIONS = {
    "OrderConfirmed": "Pedido confirmado",
    "OrderShipped": "Pedido enviado",
    "OrderCancelled": "Pedido cancelado",
    "OrderDelivered": "Pedido entregado",
    "OrderReturned": "Pedido devuelto",
}


class EventDTO(BaseModel):
    name: str = Field(examples=["OrderConfirmed"], description="Nombre técnico del Domain Event")
    description: str = Field(examples=["Pedido confirmado"], description="Qué pasó, en lenguaje sencillo")
    order_id: str
    occurred_at: datetime
    data: dict[str, Any] = Field(default_factory=dict, description="Datos propios del evento")

    @classmethod
    def from_domain(cls, event: DomainEvent) -> "EventDTO":
        extra = {
            f.name: _plain(getattr(event, f.name))
            for f in fields(event)
            if f.name not in ("order_id", "occurred_at")
        }
        return cls(name=event.name, description=EVENT_DESCRIPTIONS.get(event.name, event.name),
                   order_id=event.order_id, occurred_at=event.occurred_at, data=extra)


class OrderItemDTO(BaseModel):
    sku: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal

    @classmethod
    def from_domain(cls, item: OrderItem) -> "OrderItemDTO":
        return cls(
            sku=item.sku,
            quantity=item.quantity,
            unit_price=item.unit_price.amount,
            subtotal=item.subtotal.amount,
        )


class OrderSummaryDTO(BaseModel):
    id: str = Field(examples=["O001"])
    customer_id: str
    status: str = Field(examples=["DRAFT"], description="Código interno del estado (para programas)")
    status_label: str = Field(examples=["Pendiente de confirmar"], description="Estado para mostrar al cliente")
    units: int
    total: Decimal
    currency: str = "MXN"

    @classmethod
    def from_domain(cls, order: Order) -> "OrderSummaryDTO":
        return cls(
            id=order.id,
            customer_id=order.customer.id,
            status=order.status.value,
            status_label=order.status.label,
            units=order.units,
            total=order.total.amount,
            currency=order.total.currency,
        )


class OrderDTO(OrderSummaryDTO):
    customer_vip: bool
    customer_active: bool
    has_promotion: bool
    tracking_number: str | None = None
    cancel_reason: str | None = None
    allowed_transitions: list[str] = Field(description="Estados a los que puede pasar desde el actual")
    items: list[OrderItemDTO]
    events: list[EventDTO]

    @classmethod
    def from_domain(cls, order: Order) -> "OrderDTO":
        return cls(
            **OrderSummaryDTO.from_domain(order).model_dump(),
            customer_vip=order.customer.is_vip,
            customer_active=order.customer.active,
            has_promotion=order.has_promotion,
            tracking_number=order.tracking_number,
            cancel_reason=order.cancel_reason,
            allowed_transitions=[s.value for s in order.allowed_transitions],
            items=[OrderItemDTO.from_domain(i) for i in order.items],
            events=[EventDTO.from_domain(e) for e in order.events],
        )


class DiscountDTO(BaseModel):
    order_id: str
    percentage: Decimal = Field(examples=["10"])
    message: str = Field(description="Explicación para el cliente")
    rule: str = Field(description="Regla técnica (Specification) que se aplicó")
    total_before: Decimal
    discount: Decimal
    total_after: Decimal
    checks: list[str] = Field(description="Condiciones evaluadas: ✔ se cumple / ✘ no se cumple")

    @classmethod
    def from_domain(cls, d: DiscountDecision) -> "DiscountDTO":
        return cls(
            order_id=d.order_id,
            percentage=d.percentage,
            message=d.message,
            rule=d.rule,
            total_before=d.total_before.amount,
            discount=d.discount.amount,
            total_after=d.total_after.amount,
            checks=list(d.checks),
        )


class ErrorDTO(BaseModel):
    error: str = Field(description="Código del error (para programas)", examples=["InvalidOrderTransition"])
    mensaje: str = Field(description="Mensaje para el cliente, en lenguaje sencillo",
                         examples=["El pedido O001 ya fue confirmado; no es necesario confirmarlo otra vez."])
