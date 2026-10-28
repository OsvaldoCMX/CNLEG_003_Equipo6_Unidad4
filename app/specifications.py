"""
Specification Pattern para las reglas de descuento de TechStore.

Cada regla del negocio es un objeto con nombre que responde una sola
pregunta con `is_satisfied_by(order) -> bool`. Las reglas se combinan con:
    &  -> AND (todas deben cumplirse)
    |  -> OR  (basta una)
    ~  -> NOT (negación)

Una Specification NUNCA modifica el pedido: solo evalúa. Aplicar o
calcular el descuento es responsabilidad de DiscountService.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from app.domain import Money, Order


# ---------------------------------------------------------------------------
# Base y combinadores
# ---------------------------------------------------------------------------
class Specification(ABC):
    @abstractmethod
    def is_satisfied_by(self, order: Order) -> bool:
        raise NotImplementedError

    def __and__(self, other: "Specification") -> "Specification":
        return AndSpec(self, other)

    def __or__(self, other: "Specification") -> "Specification":
        return OrSpec(self, other)

    def __invert__(self) -> "Specification":
        return NotSpec(self)


class AndSpec(Specification):
    def __init__(self, left: Specification, right: Specification):
        self.left, self.right = left, right

    def is_satisfied_by(self, order: Order) -> bool:
        return self.left.is_satisfied_by(order) and self.right.is_satisfied_by(order)

    def __str__(self) -> str:
        return f"({self.left} AND {self.right})"


class OrSpec(Specification):
    def __init__(self, left: Specification, right: Specification):
        self.left, self.right = left, right

    def is_satisfied_by(self, order: Order) -> bool:
        return self.left.is_satisfied_by(order) or self.right.is_satisfied_by(order)

    def __str__(self) -> str:
        return f"({self.left} OR {self.right})"


class NotSpec(Specification):
    def __init__(self, spec: Specification):
        self.spec = spec

    def is_satisfied_by(self, order: Order) -> bool:
        return not self.spec.is_satisfied_by(order)

    def __str__(self) -> str:
        return f"NOT {self.spec}"


# ---------------------------------------------------------------------------
# Reglas atómicas del negocio
# ---------------------------------------------------------------------------
class MinimumAmountSpec(Specification):
    """Total ESTRICTAMENTE mayor que el umbral (500 no supera 500)."""

    def __init__(self, threshold: Decimal):
        self.threshold = Decimal(threshold)

    def is_satisfied_by(self, order: Order) -> bool:
        return order.total.amount > self.threshold

    def __str__(self) -> str:
        return f"total > {self.threshold} MXN"


class VIPCustomerSpec(Specification):
    def is_satisfied_by(self, order: Order) -> bool:
        return order.customer.is_vip

    def __str__(self) -> str:
        return "cliente VIP"


class MinimumUnitsSpec(Specification):
    """Al menos `min_units` unidades (suma de cantidades)."""

    def __init__(self, min_units: int):
        self.min_units = min_units

    def is_satisfied_by(self, order: Order) -> bool:
        return order.units >= self.min_units

    def __str__(self) -> str:
        return f"unidades >= {self.min_units}"


class ActivePromotionSpec(Specification):
    def is_satisfied_by(self, order: Order) -> bool:
        return order.has_promotion

    def __str__(self) -> str:
        return "promoción activa"


# ---------------------------------------------------------------------------
# Reglas compuestas de descuento
# ---------------------------------------------------------------------------
VIP_PERCENT = Decimal("10")
HIGH_AMOUNT_PERCENT = Decimal("5")


def vip_discount_spec() -> Specification:
    """10 %: VIP AND total > 500 AND unidades >= 4 AND NOT promoción."""
    return (
        MinimumAmountSpec(Decimal("500"))
        & VIPCustomerSpec()
        & MinimumUnitsSpec(4)
        & ~ActivePromotionSpec()
    )


def high_amount_discount_spec() -> Specification:
    """5 %: total > 5000 AND NOT promoción."""
    return MinimumAmountSpec(Decimal("5000")) & ~ActivePromotionSpec()


def any_discount_spec() -> Specification:
    """Elegible a algún descuento: regla 10 % OR regla 5 %."""
    return vip_discount_spec() | high_amount_discount_spec()


@dataclass(frozen=True)
class DiscountDecision:
    """Resultado (Value Object) de evaluar el descuento de un pedido."""

    order_id: str
    percentage: Decimal
    rule: str
    total_before: Money
    discount: Money
    total_after: Money
    checks: tuple[str, ...]
    message: str  # explicación para el cliente, sin términos técnicos


class DiscountService:
    """Servicio de dominio: decide qué descuento corresponde. No modifica el pedido."""

    def __init__(self):
        self.vip_rule = vip_discount_spec()
        self.high_amount_rule = high_amount_discount_spec()
        self.any_rule = any_discount_spec()

    def evaluate(self, order: Order) -> DiscountDecision:
        if not self.any_rule.is_satisfied_by(order):
            percentage, rule = Decimal("0"), "Sin descuento"
        elif self.vip_rule.is_satisfied_by(order):  # prioridad: 10 % gana a 5 %
            percentage, rule = VIP_PERCENT, f"10 % VIP: {self.vip_rule}"
        else:
            percentage, rule = HIGH_AMOUNT_PERCENT, f"5 % monto alto: {self.high_amount_rule}"

        total = order.total
        discount = total.percentage(percentage)
        to_pay = total.subtract(discount)
        if percentage == VIP_PERCENT:
            message = (f"¡Buenas noticias! Por ser cliente VIP, con una compra mayor a $500 y al menos "
                       f"4 piezas, este pedido tiene 10 % de descuento: ahorras {discount} y pagas {to_pay}.")
        elif percentage == HIGH_AMOUNT_PERCENT:
            message = (f"¡Buenas noticias! Por una compra mayor a $5,000, este pedido tiene 5 % de "
                       f"descuento: ahorras {discount} y pagas {to_pay}.")
        elif order.has_promotion:
            message = ("Este pedido ya cuenta con una promoción activa, por eso no se le puede aplicar "
                       f"otro descuento. Total a pagar: {to_pay}.")
        else:
            message = ("Este pedido no tiene descuento. Recuerda: 10 % para clientes VIP con compras "
                       "mayores a $500 y al menos 4 piezas, o 5 % en compras mayores a $5,000. "
                       f"Total a pagar: {to_pay}.")
        return DiscountDecision(
            order_id=order.id,
            percentage=percentage,
            rule=rule,
            total_before=total,
            discount=discount,
            total_after=to_pay,
            checks=self.explain(order),
            message=message,
        )

    @staticmethod
    def explain(order: Order) -> tuple[str, ...]:
        """Lista legible de qué condiciones atómicas se cumplen (✔) y cuáles no (✘)."""
        atomic = [
            VIPCustomerSpec(),
            MinimumAmountSpec(Decimal("500")),
            MinimumUnitsSpec(4),
            ~ActivePromotionSpec(),
            MinimumAmountSpec(Decimal("5000")),
        ]
        return tuple(f"{'✔' if s.is_satisfied_by(order) else '✘'} {s}" for s in atomic)
