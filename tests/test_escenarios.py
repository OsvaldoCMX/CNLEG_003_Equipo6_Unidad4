"""
Escenarios E01–E15 de datos/escenarios.csv.

Cada prueba carga datos NUEVOS con load_fixture() (fixture `orders`), así un
escenario que cambia estados no contamina al siguiente. No se edita ningún
pedido para forzar el resultado: se usan tal como vienen en el CSV.
"""
from decimal import Decimal

import pytest

from app.domain import (
    InvalidOrderData,
    InvalidOrderTransition,
    Money,
    OrderItem,
    OrderItemLimitExceeded,
    OrderStatus,
)
from app.specifications import DiscountService

descuento = DiscountService().evaluate


def test_e01_vip_con_4_unidades_obtiene_10(orders):
    assert descuento(orders["O001"]).percentage == Decimal("10")


def test_e02_no_vip_menor_a_5000_sin_descuento(orders):
    assert descuento(orders["O002"]).percentage == Decimal("0")


def test_e03_cliente_inactivo_no_confirma(orders):
    order = orders["O003"]
    with pytest.raises(InvalidOrderData, match="inactiva"):
        order.confirm()
    assert order.status is OrderStatus.DRAFT and order.events == ()


def test_e04_promocion_activa_bloquea_descuento(orders):
    assert descuento(orders["O005"]).percentage == Decimal("0")


def test_e05_total_mayor_a_5000_sin_promocion_obtiene_5(orders):
    assert descuento(orders["O006"]).percentage == Decimal("5")


def test_e06_pedido_vacio_no_confirma(orders):
    order = orders["O007"]
    with pytest.raises(InvalidOrderData, match="no tiene artículos"):
        order.confirm()
    assert order.status is OrderStatus.DRAFT and order.events == ()


def test_e07_veinte_unidades_se_confirman(orders):
    order = orders["O008"]
    assert order.units == 20
    order.confirm()
    assert order.status is OrderStatus.CONFIRMED
    assert [e.name for e in order.events] == ["OrderConfirmed"]


def test_e08_agregar_2_a_19_unidades_excede_limite(orders):
    order = orders["O009"]
    with pytest.raises(OrderItemLimitExceeded):
        order.add_item(OrderItem("SKU003", 2, Money(Decimal("120.00"))))
    assert order.units == 19


def test_e09_total_exactamente_500_no_supera_umbral(orders):
    order = orders["O010"]
    assert order.total.amount == Decimal("500.00")
    assert descuento(order).percentage == Decimal("0")


def test_e10_vip_con_3_unidades_sin_descuento(orders):
    order = orders["O011"]
    assert order.customer.is_vip and order.units == 3
    assert descuento(order).percentage == Decimal("0")


def test_e11_confirmar_ok_pero_enviar_sin_guia_se_rechaza(orders):
    order = orders["O012"]
    order.confirm()
    with pytest.raises(InvalidOrderData, match="guía"):
        order.ship("")
    assert order.status is OrderStatus.CONFIRMED
    assert [e.name for e in order.events] == ["OrderConfirmed"]


def test_e12_segunda_confirmacion_se_rechaza(orders):
    order = orders["O013"]
    order.confirm()
    with pytest.raises(InvalidOrderTransition):
        order.confirm()
    assert len(order.events) == 1


def test_e13_no_se_cancela_un_pedido_enviado(orders):
    order = orders["O014"]
    order.confirm()
    order.ship("TRK-O014")
    with pytest.raises(InvalidOrderTransition):
        order.cancel("Cliente lo solicitó")
    assert order.status is OrderStatus.SHIPPED
    assert [e.name for e in order.events] == ["OrderConfirmed", "OrderShipped"]


def test_e14_devolucion_termina_en_returned(orders):
    order = orders["O015"]
    order.confirm()
    order.ship("TRK-O015")
    order.return_order()
    assert order.status is OrderStatus.RETURNED
    assert [e.name for e in order.events] == ["OrderConfirmed", "OrderShipped", "OrderReturned"]


def test_e15_entrega_termina_en_delivered(orders):
    order = orders["O016"]
    order.confirm()
    order.ship("TRK-O016")
    order.deliver()
    assert order.status is OrderStatus.DELIVERED
    assert [e.name for e in order.events] == ["OrderConfirmed", "OrderShipped", "OrderDelivered"]


def test_carga_csv_16_pedidos_en_draft_y_totales_de_control(orders):
    assert len(orders) == 16
    assert all(o.status is OrderStatus.DRAFT for o in orders.values())
    assert sum(len(o.items) for o in orders.values()) == 21
    control = {"O001": (4, "2100.00"), "O006": (1, "18000.00"), "O010": (2, "500.00")}
    for pedido, (unidades, total) in control.items():
        assert orders[pedido].units == unidades
        assert orders[pedido].total.amount == Decimal(total)
