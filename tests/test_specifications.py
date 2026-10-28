"""Pruebas del Specification Pattern y de la política de descuentos."""
from decimal import Decimal

import pytest

from app.specifications import (
    ActivePromotionSpec,
    DiscountService,
    MinimumAmountSpec,
    MinimumUnitsSpec,
    Specification,
    VIPCustomerSpec,
    any_discount_spec,
    vip_discount_spec,
)
from tests.helpers import make_order


class Siempre(Specification):
    def __init__(self, valor):
        self.valor = valor

    def is_satisfied_by(self, order):
        return self.valor


V, F = Siempre(True), Siempre(False)


class TestCombinadores:
    @pytest.mark.parametrize("a, b, esperado", [(V, V, True), (V, F, False), (F, V, False), (F, F, False)])
    def test_and(self, a, b, esperado):
        assert (a & b).is_satisfied_by(None) is esperado

    @pytest.mark.parametrize("a, b, esperado", [(V, V, True), (V, F, True), (F, V, True), (F, F, False)])
    def test_or(self, a, b, esperado):
        assert (a | b).is_satisfied_by(None) is esperado

    def test_not(self):
        assert (~V).is_satisfied_by(None) is False
        assert (~F).is_satisfied_by(None) is True


class TestReglasAtomicas:
    def test_monto_minimo_es_estricto(self):
        regla = MinimumAmountSpec(Decimal("500"))
        assert not regla.is_satisfied_by(make_order(units=1, price="500.00"))
        assert regla.is_satisfied_by(make_order(units=1, price="500.01"))

    def test_unidades_minimas_cuatro(self):
        regla = MinimumUnitsSpec(4)
        assert not regla.is_satisfied_by(make_order(units=3))
        assert regla.is_satisfied_by(make_order(units=4))

    def test_cliente_vip(self):
        assert VIPCustomerSpec().is_satisfied_by(make_order(vip=True))
        assert not VIPCustomerSpec().is_satisfied_by(make_order(vip=False))

    def test_promocion_activa(self):
        assert ActivePromotionSpec().is_satisfied_by(make_order(promo=True))
        assert (~ActivePromotionSpec()).is_satisfied_by(make_order(promo=False))

    def test_evaluar_no_modifica_el_pedido(self):
        order = make_order(units=4, price="200", vip=True)
        antes = (order.status, order.items, order.total, order.events)
        any_discount_spec().is_satisfied_by(order)
        assert (order.status, order.items, order.total, order.events) == antes


class TestDescuentos:
    @pytest.fixture
    def servicio(self):
        return DiscountService()

    def test_o001_vip_recibe_10(self, orders, servicio):
        d = servicio.evaluate(orders["O001"])
        assert d.percentage == Decimal("10")
        assert d.discount.amount == Decimal("210.00")
        assert d.total_after.amount == Decimal("1890.00")

    def test_o006_monto_alto_recibe_5(self, orders, servicio):
        d = servicio.evaluate(orders["O006"])
        assert d.percentage == Decimal("5")
        assert d.total_after.amount == Decimal("17100.00")

    @pytest.mark.parametrize("pedido", ["O002", "O005", "O010", "O011"])
    def test_pedidos_sin_descuento(self, orders, servicio, pedido):
        assert servicio.evaluate(orders[pedido]).percentage == Decimal("0")

    def test_prioridad_10_cuando_cumple_ambas_reglas(self, servicio):
        order = make_order(units=4, price="1500.00", vip=True)  # 6000 > 5000 y VIP
        assert vip_discount_spec().is_satisfied_by(order)
        assert servicio.evaluate(order).percentage == Decimal("10")

    def test_promocion_bloquea_incluso_el_5(self, servicio):
        order = make_order(units=1, price="9000.00", promo=True)
        assert servicio.evaluate(order).percentage == Decimal("0")
