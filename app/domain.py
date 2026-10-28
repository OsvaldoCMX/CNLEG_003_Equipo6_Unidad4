"""
Dominio de TechStore (Unidad 4 - Componentes de Lógica de Negocio).

Aquí viven TODAS las reglas de negocio de los pedidos. Este módulo no
importa FastAPI, no lee archivos y no sabe nada de HTTP: se puede ejecutar
y probar en memoria, sin servidor. La API y el proceso de escenarios solo
llaman a los métodos de Order; nunca cambian el estado por su cuenta.

Contenido:
    - Excepciones de dominio (jerarquía DomainException).
    - Value Objects: Money y OrderItem.
    - Entidades: Customer y Order (Aggregate Root).
    - Máquina de estados de Order y Domain Events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

MAX_UNITS_PER_ORDER = 20
DEFAULT_CURRENCY = "MXN"
_CENTS = Decimal("0.01")


# ---------------------------------------------------------------------------
# Excepciones de dominio
# ---------------------------------------------------------------------------
class DomainException(Exception):
    """
    Una operación viola una regla del negocio (no es un error técnico).

    El texto de cada excepción está pensado para mostrarse TAL CUAL al cliente:
    lenguaje sencillo, en español y sin códigos internos (DRAFT, CONFIRMED...).
    """


class InvalidOrderData(DomainException):
    """Datos que rompen una invariante: monto negativo, pedido vacío, falta guía..."""


class OrderException(DomainException):
    """Errores propios del ciclo de vida de un pedido."""


class InvalidOrderTransition(OrderException):
    """La máquina de estados no permite esa operación en el estado actual."""

    def __init__(self, message: str, current: "OrderStatus", target: "OrderStatus | None" = None):
        self.current = current  # el código técnico queda disponible para el programa,
        self.target = target    # pero no aparece en el mensaje que lee el cliente
        super().__init__(message)


class OrderItemLimitExceeded(OrderException):
    """El pedido superaría el máximo de unidades permitido."""

    def __init__(self, current_units: int, requested: int, order_id: str = ""):
        self.current_units = current_units
        self.requested = requested
        available = MAX_UNITS_PER_ORDER - current_units
        pedido = f"El pedido {order_id}" if order_id else "Este pedido"
        hint = (f"puedes agregar como máximo {available} más" if available > 0
                else "ya no es posible agregar más")
        super().__init__(
            f"Un pedido puede tener como máximo {MAX_UNITS_PER_ORDER} piezas en total. "
            f"{pedido} ya tiene {current_units} y se "
            f"{'intentó agregar 1 más' if requested == 1 else f'intentaron agregar {requested}'}; {hint}."
        )


# ---------------------------------------------------------------------------
# Value Objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Money:
    """Importe monetario inmutable. Se compara por valor: Money(100) == Money(100)."""

    amount: Decimal
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self):
        amount = self.amount
        # bool es subclase de int y float pierde precisión: ambos se rechazan.
        if isinstance(amount, (bool, float)):
            raise InvalidOrderData(
                "Los importes se registran con Decimal (por ejemplo Decimal('600.00')), no con float."
            )
        if isinstance(amount, int):
            amount = Decimal(amount)
            object.__setattr__(self, "amount", amount)
        if not isinstance(amount, Decimal):
            raise InvalidOrderData("El importe debe ser un número válido.")
        if not amount.is_finite() or amount < 0:
            raise InvalidOrderData("Un importe no puede ser negativo.")
        if not isinstance(self.currency, str) or len(self.currency.strip()) != 3:
            raise InvalidOrderData("La moneda debe indicarse con un código de 3 letras, por ejemplo MXN.")
        object.__setattr__(self, "currency", self.currency.strip().upper())

    @classmethod
    def zero(cls, currency: str = DEFAULT_CURRENCY) -> "Money":
        return cls(Decimal("0.00"), currency)

    def add(self, other: "Money") -> "Money":
        self._ensure_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def subtract(self, other: "Money") -> "Money":
        self._ensure_same_currency(other)
        return Money(self.amount - other.amount, self.currency)  # negativo -> error

    def multiply(self, quantity: int) -> "Money":
        return Money(self.amount * quantity, self.currency)

    def percentage(self, percent: Decimal) -> "Money":
        """Devuelve el `percent` % de este importe, redondeado a centavos."""
        value = (self.amount * Decimal(percent) / Decimal("100")).quantize(_CENTS, ROUND_HALF_UP)
        return Money(value, self.currency)

    def _ensure_same_currency(self, other: "Money") -> None:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise InvalidOrderData("No es posible sumar o restar importes en monedas distintas.")

    def __str__(self) -> str:
        return f"${self.amount:,.2f} {self.currency}"   # ej. $2,100.00 MXN


@dataclass(frozen=True)
class OrderItem:
    """Renglón de un pedido: producto (SKU), cantidad y precio unitario."""

    sku: str
    quantity: int
    unit_price: Money

    def __post_init__(self):
        if not isinstance(self.sku, str) or not self.sku.strip():
            raise InvalidOrderData("Indica qué producto (SKU) deseas agregar.")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity <= 0:
            raise InvalidOrderData("La cantidad debe ser un número entero de al menos 1 pieza.")
        if not isinstance(self.unit_price, Money):
            raise InvalidOrderData("El precio del producto no es válido.")

    @property
    def subtotal(self) -> Money:
        # El subtotal se calcula, nunca se captura a mano.
        return self.unit_price.multiply(self.quantity)


# ---------------------------------------------------------------------------
# Entidad Customer
# ---------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class Customer:
    """Entidad: se identifica por su id, no por el valor de sus atributos."""

    id: str
    is_vip: bool = False
    active: bool = True
    name: str = ""

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise InvalidOrderData("El cliente debe tener un número de identificación.")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Customer) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)


# ---------------------------------------------------------------------------
# Máquina de estados y Domain Events
# ---------------------------------------------------------------------------
class OrderStatus(str, Enum):
    """El valor (DRAFT, CONFIRMED...) es el código interno; `label` es lo que ve el cliente."""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    RETURNED = "RETURNED"

    @property
    def label(self) -> str:
        return STATUS_LABELS[self]


STATUS_LABELS = {
    OrderStatus.DRAFT: "Pendiente de confirmar",
    OrderStatus.CONFIRMED: "Confirmado",
    OrderStatus.SHIPPED: "Enviado",
    OrderStatus.DELIVERED: "Entregado",
    OrderStatus.CANCELLED: "Cancelado",
    OrderStatus.RETURNED: "Devuelto",
}

# Cómo se explica cada estado dentro de una frase ("...porque ya fue enviado").
_WHY_BY_STATUS = {
    OrderStatus.DRAFT: "aún no ha sido confirmado",
    OrderStatus.CONFIRMED: "ya fue confirmado",
    OrderStatus.SHIPPED: "ya fue enviado",
    OrderStatus.DELIVERED: "ya fue entregado",
    OrderStatus.CANCELLED: "ya fue cancelado",
    OrderStatus.RETURNED: "ya fue devuelto",
}


def friendly_transition_message(order_id: str, action: str, current: OrderStatus,
                                target: OrderStatus | None = None) -> str:
    """Mensaje para el cliente cuando una operación no se permite en el estado actual."""
    if target is not None and target == current:            # p. ej. confirmar dos veces
        return f"El pedido {order_id} {_WHY_BY_STATUS[current]}; no es necesario {action}lo otra vez."
    why = _WHY_BY_STATUS[current]
    if current == OrderStatus.CONFIRMED and target in (OrderStatus.DELIVERED, OrderStatus.RETURNED):
        why = "aún no ha sido enviado"
    return f"No es posible {action} el pedido {order_id} porque {why}."


# Única fuente de verdad del ciclo de vida: cualquier salto que no esté aquí se rechaza.
#   DRAFT --confirm--> CONFIRMED --ship--> SHIPPED --deliver--> DELIVERED
#                          |                  |
#                       cancel             return
#                          v                  v
#                      CANCELLED          RETURNED
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.CONFIRMED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.SHIPPED, OrderStatus.CANCELLED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED, OrderStatus.RETURNED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.RETURNED: frozenset(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: object) -> str:
    """Texto sin espacios sobrantes; None o tipos no-texto cuentan como vacío."""
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Hecho del negocio que YA ocurrió. Inmutable y con fecha/hora (UTC)."""

    order_id: str
    occurred_at: datetime = field(default_factory=_now)

    @property
    def name(self) -> str:
        return type(self).__name__


@dataclass(frozen=True, kw_only=True)
class OrderConfirmed(DomainEvent):
    customer_id: str
    total: Money


@dataclass(frozen=True, kw_only=True)
class OrderShipped(DomainEvent):
    tracking_number: str


@dataclass(frozen=True, kw_only=True)
class OrderCancelled(DomainEvent):
    reason: str


@dataclass(frozen=True, kw_only=True)
class OrderDelivered(DomainEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class OrderReturned(DomainEvent):
    reason: str = ""


# ---------------------------------------------------------------------------
# Aggregate Root: Order
# ---------------------------------------------------------------------------
class Order:
    """
    Aggregate Root del pedido. Protege sus invariantes:
      * máximo 20 UNIDADES (suma de cantidades, no número de renglones);
      * solo acepta artículos mientras está en DRAFT y solo en MXN;
      * solo cambia de estado mediante sus métodos de negocio;
      * una operación rechazada no cambia el estado ni registra eventos.

    `status`, `items` y `events` son de solo lectura desde fuera:
    `pedido.status = ...` o `pedido.items.append(...)` no están permitidos.
    """

    def __init__(self, customer: Customer, id: str, has_promotion: bool = False):
        if not isinstance(customer, Customer):
            raise InvalidOrderData("El pedido debe pertenecer a un cliente registrado.")
        if not isinstance(id, str) or not id.strip():
            raise InvalidOrderData("El pedido debe tener un número de identificación.")
        self._customer = customer
        self._id = id.strip()
        self._has_promotion = bool(has_promotion)
        self._status = OrderStatus.DRAFT
        self._items: list[OrderItem] = []
        self._events: list[DomainEvent] = []
        self._tracking_number: str | None = None
        self._cancel_reason: str | None = None

    # ----- Lectura -----------------------------------------------------------
    @property
    def id(self) -> str:
        return self._id

    @property
    def customer(self) -> Customer:
        return self._customer

    @property
    def has_promotion(self) -> bool:
        return self._has_promotion

    @property
    def status(self) -> OrderStatus:
        return self._status

    @property
    def items(self) -> tuple[OrderItem, ...]:
        return tuple(self._items)

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._events)

    @property
    def tracking_number(self) -> str | None:
        return self._tracking_number

    @property
    def cancel_reason(self) -> str | None:
        return self._cancel_reason

    @property
    def units(self) -> int:
        """Suma de cantidades (no cuenta renglones)."""
        return sum(item.quantity for item in self._items)

    @property
    def total(self) -> Money:
        total = Money.zero()
        for item in self._items:
            total = total.add(item.subtotal)
        return total

    @property
    def allowed_transitions(self) -> list[OrderStatus]:
        return sorted(ALLOWED_TRANSITIONS[self._status], key=lambda s: s.value)

    # ----- Comportamiento ----------------------------------------------------
    def add_item(self, item: OrderItem) -> None:
        if self._status != OrderStatus.DRAFT:
            raise InvalidOrderTransition(
                f"Ya no es posible agregar artículos al pedido {self._id} porque "
                f"{_WHY_BY_STATUS[self._status]}.",
                self._status,
            )
        if not isinstance(item, OrderItem):
            raise InvalidOrderData("El artículo que se intenta agregar no es válido.")
        if item.unit_price.currency != DEFAULT_CURRENCY:
            raise InvalidOrderData("Solo se aceptan precios en pesos mexicanos (MXN).")
        if self.units + item.quantity > MAX_UNITS_PER_ORDER:
            raise OrderItemLimitExceeded(self.units, item.quantity, self._id)
        self._items.append(item)

    def confirm(self) -> None:
        # 1) validar  2) cambiar estado  3) registrar evento
        self._ensure_can_move_to(OrderStatus.CONFIRMED, "confirmar")
        if not self._items:
            raise InvalidOrderData(
                f"El pedido {self._id} no tiene artículos. Agrega al menos un producto antes de confirmarlo."
            )
        if not self._customer.active:
            raise InvalidOrderData(
                f"No es posible confirmar el pedido {self._id} porque la cuenta del cliente "
                f"{self._customer.id} está inactiva. Comunícate con atención a clientes."
            )
        self._status = OrderStatus.CONFIRMED
        self._record(OrderConfirmed(order_id=self._id, customer_id=self._customer.id, total=self.total))

    def ship(self, tracking_number: str | None) -> None:
        self._ensure_can_move_to(OrderStatus.SHIPPED, "enviar")
        guide = _clean_text(tracking_number)
        if not guide:
            raise InvalidOrderData(
                f"Para enviar el pedido {self._id} es necesario capturar el número de guía de la paquetería."
            )
        self._tracking_number = guide
        self._status = OrderStatus.SHIPPED
        self._record(OrderShipped(order_id=self._id, tracking_number=guide))

    def cancel(self, reason: str | None) -> None:
        self._ensure_can_move_to(OrderStatus.CANCELLED, "cancelar")
        motive = _clean_text(reason)
        if not motive:
            raise InvalidOrderData(
                f"Para cancelar el pedido {self._id} es necesario indicar el motivo de la cancelación."
            )
        self._cancel_reason = motive
        self._status = OrderStatus.CANCELLED
        self._record(OrderCancelled(order_id=self._id, reason=motive))

    def deliver(self) -> None:
        self._ensure_can_move_to(OrderStatus.DELIVERED, "entregar")
        self._status = OrderStatus.DELIVERED
        self._record(OrderDelivered(order_id=self._id))

    def return_order(self, reason: str | None = None) -> None:
        self._ensure_can_move_to(OrderStatus.RETURNED, "devolver")
        self._status = OrderStatus.RETURNED
        self._record(OrderReturned(order_id=self._id, reason=_clean_text(reason)))

    # ----- Apoyo interno -----------------------------------------------------
    def _ensure_can_move_to(self, target: OrderStatus, action: str) -> None:
        if target not in ALLOWED_TRANSITIONS[self._status]:
            raise InvalidOrderTransition(
                friendly_transition_message(self._id, action, self._status, target), self._status, target
            )

    def _record(self, event: DomainEvent) -> None:
        self._events.append(event)

    # Entidad: la identidad la da el id.
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Order) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return (
            f"Order(id={self._id!r}, customer={self._customer.id!r}, status={self._status.value}, "
            f"units={self.units}, total={self.total})"
        )
