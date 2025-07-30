import os
import time
import asyncio
from binance.client import Client
from binance.enums import *
from datetime import datetime, timedelta
from telegram import Bot
import json

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

assert API_KEY, "FALTA BINANCE_API_KEY"
assert API_SECRET, "FALTA BINANCE_API_SECRET"
assert TELEGRAM_TOKEN, "FALTA TELEGRAM_BOT_TOKEN"
assert TELEGRAM_CHAT_ID, "FALTA TELEGRAM_CHAT_ID"

client = Client(API_KEY, API_SECRET)
telegram_bot = Bot(token=TELEGRAM_TOKEN)

MARGEN_BENEFICIO = 0.005
STOP_LOSS = 0.03
TIEMPO_MAX_HORAS = 2
PERDIDA_DIARIA_LIMITE = 50
PORCENTAJE_MAX_USDT = 0.3
HORA_RESUMEN = "23:00"
HISTORIAL = "historial_operaciones.json"
MEMORIA_IA = "memoria_ia.json"

def cargar_json(path):
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return []

def guardar_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def obtener_saldo_usdt():
    cuenta = client.get_asset_balance(asset="USDT")
    return float(cuenta['free']) if cuenta else 0

def obtener_lot_info(simbolo):
    info = client.get_symbol_info(simbolo)
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            return float(f['stepSize']), float(f['minQty'])
    return 0.00001, 0.00001

def ajustar_cantidad(cantidad, step_size):
    return round(cantidad - (cantidad % step_size), 8)

def seleccionar_monedas(memoria):
    tickers = client.get_ticker()
    monedas_validas = []
    for ticker in tickers:
        simbolo = ticker['symbol']
        if "USDT" in simbolo and not any(x in simbolo for x in ["UP", "DOWN", "BUSD", "USDC", "TUSD"]):
            cambio = abs(float(ticker['priceChangePercent']))
            volumen = float(ticker['quoteVolume'])
            if cambio >= 2 and volumen >= 500000:
                peso = memoria.get(simbolo, 1)
                monedas_validas.append((simbolo, peso, cambio))
    monedas_validas.sort(key=lambda x: x[1]*x[2], reverse=True)
    return [x[0] for x in monedas_validas]

def comprar_moneda(simbolo, cantidad_usdt):
    precio = float(client.get_symbol_ticker(symbol=simbolo)['price'])
    step_size, min_qty = obtener_lot_info(simbolo)
    cantidad_bruta = cantidad_usdt / precio
    cantidad = ajustar_cantidad(cantidad_bruta, step_size)
    if cantidad < min_qty:
        telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ No se puede comprar {simbolo}. Cantidad ({cantidad}) < mínimo ({min_qty})")
        return None
    orden = client.order_market_buy(symbol=simbolo, quantity=cantidad)
    return orden

def vender_moneda(simbolo, cantidad):
    return client.order_market_sell(symbol=simbolo, quantity=cantidad)

def evaluar_ventas(historial, memoria):
    nuevas_ventas = []
    for op in historial:
        if op.get('vendido'):
            continue
        simbolo = op['simbolo']
        precio_compra = op['precio']
        cantidad = float(op['cantidad'])
        precio_actual = float(client.get_symbol_ticker(symbol=simbolo)['price'])
        tiempo_compra = datetime.fromisoformat(op['momento_compra'])
        tiempo_actual = datetime.now()
        diferencia = precio_actual - precio_compra
        variacion = diferencia / precio_compra

        if variacion >= MARGEN_BENEFICIO or variacion <= -STOP_LOSS or tiempo_actual - tiempo_compra > timedelta(hours=TIEMPO_MAX_HORAS):
            vender_moneda(simbolo, cantidad)
            op['vendido'] = True
            op['precio_venta'] = precio_actual
            op['momento_venta'] = str(datetime.now())
            nuevas_ventas.append(op)
            resultado = "✅ Ganancia" if variacion >= 0 else "❌ Pérdida"
            telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID,
                text=f"{resultado} en {simbolo}\nPrecio venta: {precio_actual:.4f} USDT\nVariación: {variacion*100:.2f}%")
            memoria[simbolo] = memoria.get(simbolo, 1) + (1 if variacion > 0 else -1)
    return nuevas_ventas

def enviar_resumen(historial):
    hoy = datetime.now().strftime("%Y-%m-%d")
    resumen = f"📊 Resumen {hoy}:\n"
    ganancias = 0
    for op in historial:
        if 'precio_venta' in op:
            ganancia = (op['precio_venta'] - op['precio']) * float(op['cantidad'])
            ganancias += ganancia
    resumen += f"Ganancia total del día: {ganancias:.2f} USDT"
    telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=resumen)

async def ciclo():
    historial = cargar_json(HISTORIAL)
    memoria = cargar_json(MEMORIA_IA)
    memoria_dict = {m['simbolo']: m['peso'] for m in memoria} if memoria else {}

    while True:
        ahora = datetime.now()
        if ahora.strftime("%H:%M") == HORA_RESUMEN:
            enviar_resumen(historial)

        saldo = obtener_saldo_usdt()
        tope = saldo * PORCENTAJE_MAX_USDT

        monedas = seleccionar_monedas(memoria_dict)
        if monedas:
            simbolo = monedas[0]
            orden = comprar_moneda(simbolo, tope)
            if orden:
                precio = float(client.get_symbol_ticker(symbol=simbolo)['price'])
                cantidad = float(orden['executedQty'])
                historial.append({
                    "simbolo": simbolo,
                    "precio": precio,
                    "cantidad": cantidad,
                    "momento_compra": str(datetime.now())
                })
                telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID,
                    text=f"📈 Compra: {simbolo}\nPrecio: {precio:.4f} USDT\nCantidad: {cantidad}")

        nuevas_ventas = evaluar_ventas(historial, memoria_dict)
        if nuevas_ventas:
            guardar_json(HISTORIAL, historial)

        memoria = [{"simbolo": k, "peso": v} for k, v in memoria_dict.items()]
        guardar_json(MEMORIA_IA, memoria)

        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ciclo())
