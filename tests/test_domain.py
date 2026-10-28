"""Pruebas del dominio: Value Objects, invariantes, máquina de estados y Domain Events."""
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.domain import (
    Customer,
    InvalidOrderData,
    InvalidOrderTransition,
    Money,
    Order,
    OrderItem,
    OrderItemLimitExceeded,
    OrderStatus,
)
from tests.helpers import make_order, mxn


# ---------------------------------------------------------------- Money ----
class TestMoney:
    def test_money_negativo_se_rechaza(self):
        with pytest.raises(InvalidOrderData):
            Money(Decimal("-1"))

    def test_money_no_acepta_float(self):
        with pytest.raises(InvalidOrderData):
            Money(600.00)

    @pytest.mark.parametrize("valor", ["NaN", "Infinity"])
    def test_money_no_acepta_valores_no_finitos(self, valor):
        with pytest.raises(InvalidOrderData):
            Money(Decimal(valor))

    def test_money_es_inmutable(self):
        precio = mxn("100")
        with pytest.raises(FrozenInstanceError):
            precio.amount = Decimal("1")

    def test_money_se_compara_por_valor(self):
        assert mxn("100") == mxn("100.00")

    def test_add_devuelve_un_objeto_nuevo_sin_modificar_operandos(self):
        a, b = mxn("100"), mxn("50")
        assert a.add(b) == mxn("150")
        assert a == mxn("100") and b == mxn("50")

    def test_add_rechaza_monedas_distintas(self):
        with pytest.raises(InvalidOrderData):
            mxn("10").add(Money(Decimal("10"), "USD"))

    def test_porcentaje_redondea_a_centavos(self):
        assert mxn("2100.00").percentage(Decimal("10")) == mxn("210.00")
        assert mxn("0.05").percentage(Decimal("10")).amount == Decimal("0.01")


# ----------------------------------------------------- OrderItem / Customer ----
class TestOrderItemYCustomer:
    @pytest.mark.parametrize("cantidad", [0, -3])
    def test_cantidad_cero_o_negativa_se_rechaza(self, cantidad):
        with pytest.raises(InvalidOrderData):
            OrderItem("SKU001", cantidad, mxn("600"))

    def test_sku_vacio_se_rechaza(self):
        with pytest.raises(InvalidOrderData):
            OrderItem("  ", 1, mxn("600"))

    def test_subtotal_se_calcula(self):
        assert OrderItem("SKU001", 2, mxn("600.00")).subtotal == mxn("1200.00")

    def test_customer_es_entidad_igualdad_por_id(self):
        assert Customer("C001", True, True) == Customer("C001", False, False)
        assert Customer("C001") != Customer("C002")


# ---------------------------------------------------------- Order (agregado) ----
class TestOrderInvariantes:
    def test_pedido_nuevo_inicia_en_draft_sin_eventos(self):
        order = make_order(units=0)
        assert order.status is OrderStatus.DRAFT
        assert order.events == ()
        assert order.total == Money.zero()

    def test_units_suma_cantidades_no_renglones(self):
        order = make_order(units=0)
        order.add_item(OrderItem("A", 3, mxn("10")))
        order.add_item(OrderItem("B", 5, mxn("10")))
        assert len(order.items) == 2
        assert order.units == 8

    def test_veinte_unidades_exactas_se_permiten(self):
        assert make_order(units=20).units == 20

    def test_unidad_21_lanza_excepcion_y_no_modifica_el_pedido(self):
        order = make_order(units=19)
        with pytest.raises(OrderItemLimitExceeded):
            order.add_item(OrderItem("X", 2, mxn("1")))
        assert order.units == 19 and len(order.items) == 1

    def test_solo_se_aceptan_precios_en_mxn(self):
        order = make_order(units=0)
        with pytest.raises(InvalidOrderData):
            order.add_item(OrderItem("X", 1, Money(Decimal("5"), "USD")))

    def test_no_se_agregan_articulos_fuera_de_draft(self):
        order = make_order()
        order.confirm()
        with pytest.raises(InvalidOrderTransition):
            order.add_item(OrderItem("X", 1, mxn("1")))

    def test_status_no_se_puede_asignar_desde_fuera(self):
        order = make_order()
        with pytest.raises(AttributeError):
            order.status = OrderStatus.SHIPPED

    def test_items_no_se_pueden_modificar_desde_fuera(self):
        order = make_order()
        with pytest.raises(AttributeError):
            order.items.append(OrderItem("X", 50, mxn("1")))  # tupla: evita saltarse el límite

    def test_pedido_requiere_cliente(self):
        with pytest.raises(InvalidOrderData):
            Order(customer=None, id="T1")


# ---------------------------------------------------- Máquina de estados ----
def test_cada_estado_tiene_etiqueta_para_el_cliente():
    assert OrderStatus.DRAFT.label == "Pendiente de confirmar"
    assert OrderStatus.SHIPPED.label == "Enviado"
    assert all(s.label and s.value not in s.label for s in OrderStatus)


def test_mensaje_de_limite_indica_cuantas_piezas_faltan():
    order = make_order(units=19)
    with pytest.raises(OrderItemLimitExceeded, match="puedes agregar como máximo 1 más"):
        order.add_item(OrderItem("X", 2, mxn("1")))


class TestTransicionesValidas:
    def test_confirmar_cambia_estado_y_registra_order_confirmed(self):
        order = make_order(units=2, price="300.00")
        order.confirm()
        assert order.status is OrderStatus.CONFIRMED
        [evento] = order.events
        assert evento.name == "OrderConfirmed"
        assert evento.customer_id == "CX" and evento.total == mxn("600.00")
        assert evento.occurred_at.tzinfo is not None

    def test_flujo_completo_hasta_delivered(self):
        order = make_order()
        order.confirm()
        order.ship("TRK-1")
        order.deliver()
        assert order.status is OrderStatus.DELIVERED
        assert order.tracking_number == "TRK-1"
        assert [e.name for e in order.events] == ["OrderConfirmed", "OrderShipped", "OrderDelivered"]

    def test_cancelar_desde_confirmed_con_motivo(self):
        order = make_order()
        order.confirm()
        order.cancel("Cliente desistió")
        assert order.status is OrderStatus.CANCELLED
        assert order.events[-1].name == "OrderCancelled"
        assert order.events[-1].reason == "Cliente desistió"

    def test_devolver_desde_shipped(self):
        order = make_order()
        order.confirm()
        order.ship("TRK-2")
        order.return_order("Llegó dañado")
        assert order.status is OrderStatus.RETURNED
        assert order.events[-1].name == "OrderReturned"


class TestReglasDeTransicion:
    def test_no_se_confirma_pedido_vacio(self):
        order = make_order(units=0)
        with pytest.raises(InvalidOrderData):
            order.confirm()
        assert order.status is OrderStatus.DRAFT and order.events == ()

    def test_no_se_confirma_con_cliente_inactivo(self):
        order = make_order(active=False)
        with pytest.raises(InvalidOrderData):
            order.confirm()
        assert order.status is OrderStatus.DRAFT and order.events == ()

    def test_confirmar_dos_veces_falla_y_no_agrega_evento(self):
        order = make_order()
        order.confirm()
        with pytest.raises(InvalidOrderTransition):
            order.confirm()
        assert len(order.events) == 1

    @pytest.mark.parametrize("guia", [None, "", "   "])
    def test_enviar_sin_guia_falla_y_sigue_confirmed(self, guia):
        order = make_order()
        order.confirm()
        with pytest.raises(InvalidOrderData):
            order.ship(guia)
        assert order.status is OrderStatus.CONFIRMED and len(order.events) == 1

    @pytest.mark.parametrize("motivo", [None, ""])
    def test_cancelar_sin_motivo_falla(self, motivo):
        order = make_order()
        order.confirm()
        with pytest.raises(InvalidOrderData):
            order.cancel(motivo)
        assert order.status is OrderStatus.CONFIRMED

    def test_pedido_enviado_no_se_puede_cancelar(self):
        order = make_order()
        order.confirm()
        order.ship("TRK-3")
        with pytest.raises(InvalidOrderTransition):
            order.cancel("Ya no lo quiero")
        assert order.status is OrderStatus.SHIPPED
        assert order.events[-1].name == "OrderShipped"


# Recorridos para llegar a cada estado y todas las operaciones posibles.
CAMINOS = {
    OrderStatus.DRAFT: [],
    OrderStatus.CONFIRMED: ["confirm"],
    OrderStatus.SHIPPED: ["confirm", "ship"],
    OrderStatus.DELIVERED: ["confirm", "ship", "deliver"],
    OrderStatus.CANCELLED: ["confirm", "cancel"],
    OrderStatus.RETURNED: ["confirm", "ship", "return_order"],
}
OPERACIONES = {
    "confirm": (lambda o: o.confirm(), OrderStatus.CONFIRMED),
    "ship": (lambda o: o.ship("TRK"), OrderStatus.SHIPPED),
    "cancel": (lambda o: o.cancel("motivo"), OrderStatus.CANCELLED),
    "deliver": (lambda o: o.deliver(), OrderStatus.DELIVERED),
    "return_order": (lambda o: o.return_order(), OrderStatus.RETURNED),
}
VALIDAS = {
    (OrderStatus.DRAFT, "confirm"),
    (OrderStatus.CONFIRMED, "ship"),
    (OrderStatus.CONFIRMED, "cancel"),
    (OrderStatus.SHIPPED, "deliver"),
    (OrderStatus.SHIPPED, "return_order"),
}
INVALIDAS = [(s, op) for s in CAMINOS for op in OPERACIONES if (s, op) not in VALIDAS]


@pytest.mark.parametrize("estado, operacion", INVALIDAS, ids=[f"{s.value}-{op}" for s, op in INVALIDAS])
def test_toda_transicion_fuera_del_diagrama_se_rechaza_sin_efectos(estado, operacion):
    order = make_order()
    for paso in CAMINOS[estado]:
        OPERACIONES[paso][0](order)
    eventos_antes = len(order.events)

    with pytest.raises(InvalidOrderTransition) as error:
        OPERACIONES[operacion][0](order)

    assert order.status is estado
    assert len(order.events) == eventos_antes
    # El mensaje es para el cliente: no muestra códigos como DRAFT o SHIPPED.
    assert not any(s.value in str(error.value) for s in OrderStatus)


@pytest.mark.parametrize("estado, operacion", sorted(VALIDAS, key=str))
def test_cada_transicion_valida_llega_al_estado_esperado(estado, operacion):
    order = make_order()
    for paso in CAMINOS[estado]:
        OPERACIONES[paso][0](order)
    funcion, destino = OPERACIONES[operacion]
    funcion(order)
    assert order.status is destino
