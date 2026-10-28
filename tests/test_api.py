"""Pruebas de la API REST: códigos HTTP semánticos y controladores delgados."""


def test_lista_los_16_pedidos(client):
    r = client.get("/orders")
    assert r.status_code == 200
    assert len(r.json()) == 16
    assert {o["status"] for o in r.json()} == {"DRAFT"}


def test_consulta_o001(client):
    r = client.get("/orders/O001")
    assert r.status_code == 200
    body = r.json()
    assert body["units"] == 4 and body["total"] == "2100.00"
    assert body["allowed_transitions"] == ["CONFIRMED"]
    assert body["status"] == "DRAFT" and body["status_label"] == "Pendiente de confirmar"


def test_pedido_inexistente_responde_404(client):
    r = client.get("/orders/O999")
    assert r.status_code == 404
    assert r.json()["error"] == "NotFound"


def test_confirmar_inexistente_responde_404(client):
    assert client.post("/orders/O999/confirm").status_code == 404


def test_confirmar_ok_200_y_evento(client):
    r = client.post("/orders/O001/confirm")
    assert r.status_code == 200
    assert r.json()["status"] == "CONFIRMED"
    assert [e["name"] for e in r.json()["events"]] == ["OrderConfirmed"]


def test_confirmar_dos_veces_responde_409(client):
    client.post("/orders/O013/confirm")
    r = client.post("/orders/O013/confirm")
    assert r.status_code == 409
    assert r.json() == {
        "error": "InvalidOrderTransition",
        "mensaje": "El pedido O013 ya fue confirmado; no es necesario confirmarlo otra vez.",
    }


def test_confirmar_pedido_vacio_responde_422(client):
    r = client.post("/orders/O007/confirm")
    assert r.status_code == 422
    assert r.json()["error"] == "InvalidOrderData"


def test_confirmar_cliente_inactivo_responde_422(client):
    assert client.post("/orders/O003/confirm").status_code == 422


def test_enviar_sin_guia_422_y_con_guia_200(client):
    client.post("/orders/O012/confirm")
    assert client.post("/orders/O012/ship", json={"tracking_number": ""}).status_code == 422
    assert client.post("/orders/O012/ship").status_code == 422
    r = client.post("/orders/O012/ship", json={"tracking_number": "TRK-12"})
    assert r.status_code == 200 and r.json()["status"] == "SHIPPED"


def test_cancelar_desde_shipped_responde_409(client):
    client.post("/orders/O014/confirm")
    client.post("/orders/O014/ship", json={"tracking_number": "TRK-14"})
    r = client.post("/orders/O014/cancel", json={"reason": "Ya no lo quiero"})
    assert r.status_code == 409
    assert client.get("/orders/O014").json()["status"] == "SHIPPED"


def test_cancelar_sin_motivo_responde_422(client):
    client.post("/orders/O002/confirm")
    assert client.post("/orders/O002/cancel", json={"reason": " "}).status_code == 422


def test_entregar_y_devolver(client):
    for pedido, accion, final in [("O016", "deliver", "DELIVERED"), ("O015", "return", "RETURNED")]:
        client.post(f"/orders/{pedido}/confirm")
        client.post(f"/orders/{pedido}/ship", json={"tracking_number": "TRK"})
        r = client.post(f"/orders/{pedido}/{accion}")
        assert r.status_code == 200 and r.json()["status"] == final


def test_agregar_unidades_sobre_el_limite_responde_422(client):
    r = client.post("/orders/O009/items", json={"sku": "SKU003", "quantity": 2})
    assert r.status_code == 422
    assert r.json()["error"] == "OrderItemLimitExceeded"
    assert client.get("/orders/O009").json()["units"] == 19


def test_agregar_producto_inexistente_responde_404(client):
    assert client.post("/orders/O007/items", json={"sku": "NOPE", "quantity": 1}).status_code == 404


def test_crear_pedido_201_y_agregar_articulo(client):
    r = client.post("/orders", json={"customer_id": "C007"})
    assert r.status_code == 201
    nuevo = r.json()["id"]
    assert nuevo == "O017"
    r = client.post(f"/orders/{nuevo}/items", json={"sku": "SKU004", "quantity": 2})
    assert r.status_code == 200 and r.json()["total"] == "1700.00"


def test_crear_pedido_cliente_inexistente_404(client):
    assert client.post("/orders", json={"customer_id": "C999"}).status_code == 404


def test_descuento_por_api(client):
    r = client.get("/orders/O001/discount")
    assert r.status_code == 200
    assert r.json()["percentage"] == "10"
    assert r.json()["total_after"] == "1890.00"


def test_eventos_publicados_solo_tras_operaciones_exitosas(client):
    client.post("/orders/O007/confirm")  # falla: vacío
    assert client.get("/events").json() == []
    client.post("/orders/O001/confirm")
    eventos = client.get("/events").json()
    assert [e["name"] for e in eventos] == ["OrderConfirmed"]
    assert eventos[0]["data"]["total"] == "2100.00 MXN"
    assert len(client.get("/notifications").json()) == 1


def test_reset_restaura_estado_inicial(client):
    client.post("/orders/O001/confirm")
    assert client.post("/admin/reset").json() == {"status": "ok", "orders": 16}
    assert client.get("/orders/O001").json()["status"] == "DRAFT"


def test_contrato_openapi_disponible(client):
    spec = client.get("/openapi.json").json()
    for ruta in ["/orders/{order_id}", "/orders/{order_id}/confirm", "/orders/{order_id}/ship",
                 "/orders/{order_id}/cancel"]:
        assert ruta in spec["paths"]
    assert "409" in spec["paths"]["/orders/{order_id}/confirm"]["post"]["responses"]


# ---------------------------------------------------------------------------
# Mensajes amigables: el cliente nunca ve códigos internos de estado
# ---------------------------------------------------------------------------
CODIGOS_INTERNOS = ["DRAFT", "CONFIRMED", "SHIPPED", "DELIVERED", "CANCELLED", "RETURNED"]


def _rechazos(client):
    """Provoca todos los tipos de rechazo y devuelve las respuestas."""
    respuestas = [
        client.get("/orders/O999"),
        client.post("/orders/O007/confirm"),
        client.post("/orders/O003/confirm"),
        client.post("/orders/O001/ship", json={"tracking_number": "TRK"}),
        client.post("/orders/O001/cancel", json={"reason": "x"}),
        client.post("/orders/O009/items", json={"sku": "SKU003", "quantity": 2}),
        client.post("/orders/O007/items", json={"sku": "SKU003", "quantity": 0}),
        client.post("/orders/O007/items", json={"sku": "SKU003", "quantity": "dos"}),
    ]
    client.post("/orders/O013/confirm")
    respuestas += [
        client.post("/orders/O013/confirm"),
        client.post("/orders/O013/deliver"),
        client.post("/orders/O013/ship", json={"tracking_number": ""}),
        client.post("/orders/O013/items", json={"sku": "SKU001", "quantity": 1}),
    ]
    client.post("/orders/O013/ship", json={"tracking_number": "TRK-13"})
    respuestas.append(client.post("/orders/O013/cancel", json={"reason": "ya no"}))
    return respuestas


def test_mensajes_de_error_sin_codigos_internos(client):
    for r in _rechazos(client):
        assert r.status_code in (404, 409, 422)
        mensaje = r.json()["mensaje"]
        assert mensaje.endswith(".")
        assert not any(codigo in mensaje for codigo in CODIGOS_INTERNOS), mensaje


def test_error_de_formato_se_explica_en_espanol(client):
    r = client.post("/orders/O007/items", json={"sku": "SKU003", "quantity": "dos"})
    assert r.status_code == 422
    assert r.json() == {
        "error": "DatosInvalidos",
        "mensaje": "Revisa la información enviada: 'quantity' debe ser un número entero.",
    }


def test_estado_y_eventos_con_texto_para_el_cliente(client):
    client.post("/orders/O015/confirm")
    body = client.post("/orders/O015/ship", json={"tracking_number": "TRK-15"}).json()
    assert body["status_label"] == "Enviado"
    assert [e["description"] for e in body["events"]] == ["Pedido confirmado", "Pedido enviado"]


def test_descuento_explicado_al_cliente(client):
    mensaje = client.get("/orders/O001/discount").json()["message"]
    assert "10 %" in mensaje and "$1,890.00 MXN" in mensaje


def test_notificacion_saluda_al_cliente_por_su_nombre(client):
    client.post("/orders/O001/confirm")
    assert client.get("/notifications").json() == [
        "Hola Ana Torres: confirmamos tu pedido O001 por $2,100.00 MXN. ¡Gracias por tu compra!"
    ]
