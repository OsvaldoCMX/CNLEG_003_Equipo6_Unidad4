"""Funciones de apoyo para construir datos de prueba sin depender del CSV."""
from decimal import Decimal

from app.domain import Customer, Money, Order, OrderItem


def mxn(value: str) -> Money:
    return Money(Decimal(value))


def make_order(units: int = 1, price: str = "100.00", vip: bool = False, active: bool = True,
               promo: bool = False, order_id: str = "T001") -> Order:
    """Pedido de apoyo para pruebas unitarias (sin depender del CSV)."""
    order = Order(customer=Customer("CX", vip, active), id=order_id, has_promotion=promo)
    if units:
        order.add_item(OrderItem("SKU-T", units, mxn(price)))
    return order
