"""
Application Service: coordina los casos de uso.

Flujo de cada caso de uso:
    1. obtener el pedido del repositorio (o fallar con NotFound);
    2. invocar el método de negocio de Order (el dominio decide si es válido);
    3. guardar;
    4. publicar los Domain Events nuevos.

No hay reglas de negocio aquí: no se compara status ni se calculan descuentos.
"""
from __future__ import annotations

from typing import Callable

from app.domain import (
    Order,
    OrderCancelled,
    OrderConfirmed,
    OrderDelivered,
    OrderItem,
    OrderReturned,
    OrderShipped,
)
from app.infrastructure import Catalog, EmailNotifier, EventBus, InMemoryOrderRepository
from app.specifications import DiscountDecision, DiscountService


class ApplicationError(Exception):
    """Error del caso de uso (no es una regla de negocio)."""


class NotFound(ApplicationError):
    """El recurso solicitado no existe (pedido, cliente o producto)."""


class OrderService:
    def __init__(
        self,
        repository: InMemoryOrderRepository,
        catalog: Catalog,
        bus: EventBus,
        discounts: DiscountService | None = None,
        notifier: EmailNotifier | None = None,
    ):
        self.repository = repository
        self.catalog = catalog
        self.bus = bus
        self.discounts = discounts or DiscountService()
        self.notifier = notifier

    # ----- Consultas -------------------------------------------------------
    def get(self, order_id: str) -> Order:
        order = self.repository.get(order_id)
        if order is None:
            raise NotFound(f"No encontramos el pedido {order_id}. Verifica el número e intenta de nuevo.")
        return order

    def list(self) -> list[Order]:
        return self.repository.list()

    def evaluate_discount(self, order_id: str) -> DiscountDecision:
        return self.discounts.evaluate(self.get(order_id))

    # ----- Comandos ---------------------------------------------------------
    def create(self, customer_id: str, has_promotion: bool = False) -> Order:
        customer = self.catalog.customers.get(customer_id)
        if customer is None:
            raise NotFound(f"No encontramos al cliente {customer_id}. Verifica el número de cliente.")
        order = Order(customer=customer, id=self.repository.next_id(), has_promotion=has_promotion)
        self.repository.save(order)
        return order

    def add_item(self, order_id: str, sku: str, quantity: int) -> Order:
        product = self.catalog.products.get(sku)
        if product is None:
            raise NotFound(f"No encontramos el producto {sku} en el catálogo.")
        return self._execute(order_id, lambda o: o.add_item(OrderItem(sku, quantity, product.price)))

    def confirm(self, order_id: str) -> Order:
        return self._execute(order_id, lambda o: o.confirm())

    def ship(self, order_id: str, tracking_number: str | None) -> Order:
        return self._execute(order_id, lambda o: o.ship(tracking_number))

    def cancel(self, order_id: str, reason: str | None) -> Order:
        return self._execute(order_id, lambda o: o.cancel(reason))

    def deliver(self, order_id: str) -> Order:
        return self._execute(order_id, lambda o: o.deliver())

    def return_order(self, order_id: str, reason: str | None = None) -> Order:
        return self._execute(order_id, lambda o: o.return_order(reason))

    def _execute(self, order_id: str, operation: Callable[[Order], None]) -> Order:
        order = self.get(order_id)
        already_published = len(order.events)
        operation(order)  # si el dominio rechaza, la excepción sale y no se publica nada
        self.repository.save(order)
        self.bus.publish(order.events[already_published:])
        return order


def build_service() -> OrderService:
    """Arma el servicio con los datos del paquete (estado limpio: 16 pedidos DRAFT)."""
    repository = InMemoryOrderRepository.from_fixture()
    catalog = Catalog.from_csv()

    def customer_name(order_id: str) -> str:
        order = repository.get(order_id)
        customer = catalog.customers.get(order.customer.id) if order else None
        return customer.name if customer and customer.name else "cliente"

    bus = EventBus()
    notifier = EmailNotifier(customer_name)
    bus.subscribe(OrderConfirmed, notifier.on_order_confirmed)
    bus.subscribe(OrderShipped, notifier.on_order_shipped)
    bus.subscribe(OrderCancelled, notifier.on_order_cancelled)
    bus.subscribe(OrderDelivered, notifier.on_order_delivered)
    bus.subscribe(OrderReturned, notifier.on_order_returned)
    return OrderService(repository, catalog, bus, notifier=notifier)
