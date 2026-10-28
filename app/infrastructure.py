"""
Infraestructura en memoria: catálogo (clientes/productos), repositorio de
pedidos y bus de Domain Events con un suscriptor de correo simulado.

Nada de esto contiene reglas de negocio; si mañana se cambia por una base de
datos o un broker de mensajes, app/domain.py no se toca.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from app.domain import (
    Customer,
    DomainEvent,
    Money,
    Order,
    OrderCancelled,
    OrderConfirmed,
    OrderDelivered,
    OrderReturned,
    OrderShipped,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "datos"


def _rows(filename: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Catálogo (solo lectura)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Money
    stock: int  # solo informativo: el laboratorio no descuenta inventario


class Catalog:
    def __init__(self, customers: dict[str, Customer], products: dict[str, Product]):
        self.customers = customers
        self.products = products

    @classmethod
    def from_csv(cls) -> "Catalog":
        customers = {
            r["cliente_id"]: Customer(
                r["cliente_id"], bool(int(r["es_vip"])), bool(int(r["activo"])), r["nombre"]
            )
            for r in _rows("clientes.csv")
        }
        products = {
            r["sku"]: Product(r["sku"], r["nombre"], Money(Decimal(r["precio_mxn"])), int(r["existencia"]))
            for r in _rows("productos.csv")
        }
        return cls(customers, products)


# ---------------------------------------------------------------------------
# Repositorio en memoria
# ---------------------------------------------------------------------------
class InMemoryOrderRepository:
    """Guarda pedidos en un diccionario. Los cambios se pierden al reiniciar."""

    def __init__(self, orders: dict[str, Order] | None = None):
        self._orders: dict[str, Order] = dict(orders or {})

    @classmethod
    def from_fixture(cls) -> "InMemoryOrderRepository":
        from cargar_csv import load_fixture  # cargador entregado por el profesor

        return cls(load_fixture())

    def get(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def list(self) -> list[Order]:
        return sorted(self._orders.values(), key=lambda o: o.id)

    def save(self, order: Order) -> None:
        self._orders[order.id] = order

    def next_id(self) -> str:
        numbers = [int(i[1:]) for i in self._orders if i[:1] == "O" and i[1:].isdigit()]
        return f"O{max(numbers, default=0) + 1:03d}"


# ---------------------------------------------------------------------------
# Publicación de Domain Events
# ---------------------------------------------------------------------------
Handler = Callable[[DomainEvent], None]


class EventBus:
    """Publica eventos a suscriptores desacoplados. Order no conoce a los suscriptores."""

    def __init__(self):
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        self.published: list[DomainEvent] = []
        self.failures: list[str] = []

    def subscribe(self, event_type: type, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, events) -> None:
        for event in events:
            self.published.append(event)
            for event_type, handlers in self._handlers.items():
                if isinstance(event, event_type):
                    for handler in handlers:
                        try:
                            handler(event)
                        except Exception as exc:  # un suscriptor caído no invalida el hecho
                            self.failures.append(f"{event.name}: {exc}")


class EmailNotifier:
    """
    Suscriptor simulado: en lugar de enviar correos, los guarda en `outbox`.
    Los mensajes son para el cliente, así que no usan códigos internos.
    """

    def __init__(self, customer_name: Callable[[str], str] = lambda order_id: "cliente"):
        self._customer_name = customer_name
        self.outbox: list[str] = []

    def _send(self, order_id: str, text: str) -> None:
        self.outbox.append(f"Hola {self._customer_name(order_id)}: {text}")

    def on_order_confirmed(self, event: OrderConfirmed) -> None:
        self._send(event.order_id, f"confirmamos tu pedido {event.order_id} por {event.total}. "
                                   "¡Gracias por tu compra!")

    def on_order_shipped(self, event: OrderShipped) -> None:
        self._send(event.order_id, f"tu pedido {event.order_id} ya va en camino. "
                                   f"Número de guía: {event.tracking_number}.")

    def on_order_cancelled(self, event: OrderCancelled) -> None:
        self._send(event.order_id, f"tu pedido {event.order_id} fue cancelado. Motivo: {event.reason}.")

    def on_order_delivered(self, event: OrderDelivered) -> None:
        self._send(event.order_id, f"tu pedido {event.order_id} fue entregado. ¡Que lo disfrutes!")

    def on_order_returned(self, event: OrderReturned) -> None:
        self._send(event.order_id, f"recibimos la devolución de tu pedido {event.order_id}.")
