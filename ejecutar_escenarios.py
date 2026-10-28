"""
Ejecuta los 15 escenarios de datos/escenarios.csv y genera la bitácora.

    python ejecutar_escenarios.py

Para cada caso se cargan datos NUEVOS con load_fixture() (un caso no afecta
al siguiente), se ejecuta la operación indicada usando SOLO los métodos del
dominio y se registra: precondiciones, estado inicial, operación, resultado
real, estado final, eventos emitidos y la prueba pytest que lo cubre.

Salidas: evidencias/bitacora_escenarios.md y evidencias/bitacora_escenarios.csv
"""
from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from app.domain import DomainException, Money, Order, OrderItem
from app.specifications import DiscountService
from cargar_csv import load_fixture

ROOT = Path(__file__).resolve().parent
DATOS = ROOT / "datos"
SALIDA = ROOT / "evidencias"
PRUEBAS = ROOT / "tests" / "test_escenarios.py"


def leer_csv(nombre: str) -> list[dict[str, str]]:
    with (DATOS / nombre).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


PRECIOS = {r["sku"]: Decimal(r["precio_mxn"]) for r in leer_csv("productos.csv")}


@dataclass
class Paso:
    descripcion: str  # llamada al dominio, p. ej. "ship('')"
    ejecutar: Callable[[], None]
    etiqueta: str     # cómo se nombra el rechazo de este paso en escenarios.csv


def construir_pasos(accion: str, order: Order) -> list[Paso]:
    """Traduce el texto de la columna `accion` a llamadas del dominio."""
    accion = accion.strip().lower()
    agregar = re.fullmatch(r"agregar (\w+) x(\d+)", accion, flags=re.IGNORECASE)
    if agregar:
        sku, cantidad = agregar.group(1).upper(), int(agregar.group(2))
        item = OrderItem(sku, cantidad, Money(PRECIOS[sku]))
        return [Paso(f"add_item({sku} x{cantidad})", lambda: order.add_item(item), "agregar")]

    guia = f"TRK-{order.id}"
    catalogo = {
        "confirmar": [Paso("confirm()", order.confirm, "confirmacion")],
        "confirmar dos veces": [Paso("confirm()", order.confirm, "primera"),
                                Paso("confirm()", order.confirm, "segunda")],
        "enviar": [Paso(f"ship('{guia}')", lambda: order.ship(guia), "envio")],
        "enviar sin guia": [Paso("ship('')", lambda: order.ship(""), "envio")],
        "cancelar": [Paso("cancel('Cliente lo solicitó')", lambda: order.cancel("Cliente lo solicitó"),
                          "cancelacion")],
        "devolver": [Paso("return_order()", order.return_order, "devolucion")],
        "entregar": [Paso("deliver()", order.deliver, "entrega")],
    }
    pasos: list[Paso] = []
    for parte in re.split(r",\s*|\s+y\s+", accion):
        if parte not in catalogo:
            raise ValueError(f"Acción no reconocida en escenarios.csv: {parte!r}")
        pasos.extend(catalogo[parte])
    return pasos


def precondiciones(order: Order) -> str:
    c = order.customer
    return (
        f"{c.id} {'VIP' if c.is_vip else 'no VIP'}, {'activo' if c.active else 'INACTIVO'}; "
        f"{order.units} u; {order.total}; {'con' if order.has_promotion else 'sin'} promoción"
    )


def pruebas_por_caso() -> dict[str, str]:
    nombres = re.findall(r"^def (test_(e\d\d)_\w+)", PRUEBAS.read_text(encoding="utf-8"), re.M)
    return {caso.upper(): nombre for nombre, caso in nombres}


def ejecutar_caso(esc: dict[str, str], pruebas: dict[str, str]) -> dict[str, str]:
    order = load_fixture()[esc["pedido_id"]]  # datos frescos en cada caso
    fila = {
        "caso": esc["caso_id"],
        "pedido": order.id,
        "precondiciones": precondiciones(order),
        "estado_inicial": order.status.value,
        "operacion": esc["accion"],
        "objetivo": esc["objetivo"],
        "prueba": pruebas.get(esc["caso_id"], "—"),
    }

    if esc["accion"].strip() == "evaluar_descuento":
        d = DiscountService().evaluate(order)
        pct = f"{d.percentage.normalize():f}%"
        fila.update(
            resultado_real=pct,
            detalle=f"{d.rule}. Descuento {d.discount} → total {d.total_after}. "
                    f"Condiciones: {'; '.join(d.checks)}",
            estado_final=order.status.value,
            eventos="— (evaluar no cambia estado)",
            cumple="Sí" if pct == esc["objetivo"].strip() else "NO",
        )
        return fila

    pasos = construir_pasos(esc["accion"], order)
    bitacora, error = [], None
    for paso in pasos:
        estado_antes, eventos_antes = order.status, len(order.events)
        try:
            paso.ejecutar()
            nuevos = [e.name for e in order.events[eventos_antes:]]
            bitacora.append(f"{paso.descripcion} ✔ → {order.status.value}"
                            + (f" [{', '.join(nuevos)}]" if nuevos else ""))
        except DomainException as exc:
            sin_efecto = order.status == estado_antes and len(order.events) == eventos_antes
            error = exc
            bitacora.append(
                f"{paso.descripcion} ✘ {type(exc).__name__}: {exc}"
                + (" (estado y eventos sin cambio)" if sin_efecto else " (¡EFECTO INDEBIDO!)")
            )
            break

    # Un "rechazo X" solo cuenta si los pasos anteriores a X sí tuvieron éxito.
    ok_previos = error is None or len(bitacora) == len(pasos)
    if error is None:
        resultado = "exito"
    elif len(pasos) == 1:
        resultado = "rechazo"
    else:
        resultado = f"rechazo {pasos[len(bitacora) - 1].etiqueta}"
    objetivo = esc["objetivo"].strip()
    fila.update(
        resultado_real=resultado,
        detalle=" | ".join(bitacora),
        estado_final=order.status.value,
        eventos=", ".join(e.name for e in order.events) or "ninguno",
        cumple="Sí" if ok_previos and resultado == objetivo else "NO",
    )
    return fila


COLUMNAS = [
    ("caso", "Caso"), ("pedido", "Pedido"), ("precondiciones", "Precondiciones"),
    ("estado_inicial", "Estado inicial"), ("operacion", "Operación"), ("objetivo", "Objetivo"),
    ("resultado_real", "Resultado real"), ("estado_final", "Estado final"),
    ("eventos", "Eventos emitidos"), ("detalle", "Explicación (salida del dominio)"),
    ("cumple", "¿Cumple?"), ("prueba", "Prueba pytest"),
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # acentos y ✔ en la terminal de Windows
    pruebas = pruebas_por_caso()
    filas = [ejecutar_caso(e, pruebas) for e in leer_csv("escenarios.csv")]

    print(f"{'Caso':<5}{'Pedido':<7}{'Inicial':<8}{'Operación':<30}{'Objetivo':<21}"
          f"{'Real':<21}{'Final':<11}{'OK':<3}")
    print("-" * 106)
    for f in filas:
        print(f"{f['caso']:<5}{f['pedido']:<7}{f['estado_inicial']:<8}{f['operacion']:<30}"
              f"{f['objetivo']:<21}{f['resultado_real']:<21}{f['estado_final']:<11}{f['cumple']:<3}")
    aciertos = sum(f["cumple"] == "Sí" for f in filas)
    print("-" * 106)
    print(f"{aciertos}/{len(filas)} escenarios con el resultado esperado")

    SALIDA.mkdir(exist_ok=True)
    with (SALIDA / "bitacora_escenarios.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[k for k, _ in COLUMNAS])
        w.writeheader()
        w.writerows(filas)
    lineas = [
        "# Bitácora de escenarios E01–E15",
        "",
        "Generada con `python ejecutar_escenarios.py` (datos recargados en cada caso).",
        "",
        "| " + " | ".join(t for _, t in COLUMNAS) + " |",
        "|" + "---|" * len(COLUMNAS),
    ]
    for f in filas:
        lineas.append("| " + " | ".join(str(f[k]).replace("|", "/") for k, _ in COLUMNAS) + " |")
    lineas += ["", f"**Resultado: {aciertos}/{len(filas)} escenarios con el resultado esperado.**", ""]
    (SALIDA / "bitacora_escenarios.md").write_text("\n".join(lineas), encoding="utf-8")
    return 0 if aciertos == len(filas) else 1


if __name__ == "__main__":
    raise SystemExit(main())
