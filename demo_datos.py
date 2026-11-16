# PeajeControl Go - datos FICTICIOS para la demo de solo lectura.
# Modulo "puro": no toca la base ni importa app.py. Genera, con semilla fija, la flota, las facturas de peaje,
# los resumenes de tarjeta y un PDF por cada documento (con la marca "EJEMPLO - NO VALIDO").
# app.sembrar_demo() es quien los guarda en la base. Nada de aca sale de datos reales.
import hashlib, random
from datetime import date, timedelta

NOMBRE_EMPRESA = "Logística Ejemplo SRL"
MARCA = "EJEMPLO – NO VÁLIDO"
SEMILLA = 20260101

RESPONSABLES = ["Carlos Ejemplo", "Laura Muestra", "Diego Prueba", "Marta Modelo", "Pablo Ficticio", "Ana Simulada"]
MARCAS_TRACTOR = ["IVECO", "SCANIA", "VOLVO", "MERCEDES BENZ"]
TAGS_SIN_FLOTA = ["00990001", "00990002"]   # pasan por peajes pero no estan en la flota (quedan "Sin control")
PASES_SIN_FLOTA = (7, 5)                    # cuantos pases tiene cada uno

# (emisor, estaciones con su tarifa). Los emisores salen del mismo orden que usa el lector de facturas.
EMISORES = {
    "AUSA": [("PEAJE ALBERDI", 4200.00), ("PEAJE DELLEPIANE", 4200.00), ("PEAJE RETIRO", 5100.00),
             ("PEAJE SARMIENTO", 3800.00), ("PEAJE ILLIA", 5100.00), ("PEAJE 25 DE MAYO", 4600.00)],
    "AUSOL": [("PEAJE GARIN", 2900.00), ("PEAJE PACHECO", 2900.00), ("PEAJE TORTUGUITAS", 3400.00),
              ("PEAJE BOULOGNE", 2500.00), ("PEAJE PILAR", 3900.00)],
    "AUBASA": [("PEAJE HUDSON", 5600.00), ("PEAJE SAMBOROMBON", 8900.00), ("PEAJE DOLORES", 9400.00),
               ("PEAJE GONNET", 4800.00)],
}
ORDEN_EMISORES = ["AUSA", "AUSOL", "AUBASA"]
PASES_POR_FACTURA = [70, 64, 60, 68, 66, 64, 72, 70, 66]   # 600 pases en total, 3 meses x 3 emisores
FACTURA_CON_DIFERENCIA = 4        # la numero 5 (AUSOL, 2do mes): el total declarado no coincide con la suma
DIFERENCIA = 12480.00

# (concepto, categoria, monto minimo, monto maximo). Las categorias coinciden con app.categorizar().
GASTOS_MODELO = [
    ("PEAJE AUTOPISTA ACCESO NORTE", "PEAJE", 2500, 6500),
    ("RECARGA TELEPEAJE", "PEAJE", 15000, 40000),
    ("ESTACION DE SERVICIO RUTA 9 GASOIL", "COMBUSTIBLE", 90000, 260000),
    ("ESTACION DE SERVICIO RUTA 5 GASOIL", "COMBUSTIBLE", 90000, 260000),
    ("GOMERIA CENTRAL NEUMATICOS", "MANTENIMIENTO", 40000, 190000),
    ("TALLER MECANICO NORTE", "MANTENIMIENTO", 25000, 140000),
    ("REPUESTOS DEL OESTE FILTRO ACEITE", "MANTENIMIENTO", 12000, 85000),
    ("COMEDOR RUTA 8 VIATICOS", "GASTO", 6000, 28000),
    ("HOTEL RUTERO ALOJAMIENTO", "GASTO", 30000, 75000),
    ("LIBRERIA INSUMOS OFICINA", "GASTO", 5000, 32000),
]
RESUMENES = [("Visa Ejemplo", RESPONSABLES[0]), ("Mastercard Ejemplo", RESPONSABLES[1])]
GASTOS_POR_RESUMEN = 40


def _ar(n):
    """Dinero en formato argentino: 1.234,50"""
    return f"{n:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _fecha(d):
    return d.strftime("%d/%m/%Y")


def _ultimos_tres_meses(hoy):
    """Los 3 meses completos anteriores a `hoy`, del mas viejo al mas nuevo: [(anio, mes), ...]."""
    a, m = hoy.year, hoy.month
    meses = []
    for _ in range(3):
        m -= 1
        if m == 0: a, m = a - 1, 12
        meses.append((a, m))
    return meses[::-1]


def _dias_del_mes(a, m):
    siguiente = date(a + (m == 12), 1 if m == 12 else m + 1, 1)
    return (siguiente - date(a, m, 1)).days


# ---------------- PDF minimo escrito a mano (sin dependencias; mismo contenido = mismos bytes) ----------------
def _esc(t):
    return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(titulo, subtitulo, lineas, pie):
    """PDF A4 con tipografia estandar. `lineas`: lista de (texto, negrita). Cada hoja lleva la marca en grande y arriba."""
    POR_HOJA = 50
    bloques = [lineas[i:i + POR_HOJA] for i in range(0, len(lineas), POR_HOJA)] or [[]]
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", None,
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold/Encoding/WinAnsiEncoding>>",
            b"<</Type/Font/Subtype/Type1/BaseFont/Courier/Encoding/WinAnsiEncoding>>",
            b"<</Type/Font/Subtype/Type1/BaseFont/Courier-Bold/Encoding/WinAnsiEncoding>>"]
    kids = []
    for n, bloque in enumerate(bloques):
        op = ["q 0.90 g BT /F2 52 Tf 0.7071 0.7071 -0.7071 0.7071 105 230 Tm (" + _esc(MARCA) + ") Tj ET Q",
              "BT /F2 15 Tf 0.89 0.02 0.07 rg 50 800 Td (" + _esc(MARCA) + ") Tj ET",
              "BT /F2 12 Tf 0 g 50 780 Td (" + _esc(titulo) + ") Tj ET",
              "BT /F1 9 Tf 0.35 g 50 766 Td (" + _esc(subtitulo) + ") Tj ET", "0 g"]
        y = 744
        for texto, negrita in bloque:
            op.append(f"BT /{'F4' if negrita else 'F3'} 9 Tf 50 {y} Td ({_esc(texto)}) Tj ET")
            y -= 13.5
        op.append(f"BT /F1 8 Tf 0.35 g 50 30 Td ({_esc(pie)}  -  Hoja {n + 1} de {len(bloques)}) Tj ET")
        flujo = "\n".join(op).encode("cp1252", "replace")
        objs.append(b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Resources<</Font<</F1 3 0 R/F2 4 0 R/F3 5 0 R/F4 6 0 R>>>>"
                    b"/Contents " + str(8 + 2 * n).encode() + b" 0 R>>")
        objs.append(b"<</Length " + str(len(flujo)).encode() + b">>\nstream\n" + flujo + b"\nendstream")
        kids.append(f"{7 + 2 * n} 0 R")
    objs[1] = ("<</Type/Pages/Kids[" + " ".join(kids) + f"]/Count {len(kids)}>>").encode()
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    ini = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs: out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{ini}\n%%EOF\n".encode()
    return bytes(out)


# ---------------- generacion ----------------
def _flota(rng):
    numeros = rng.sample(range(100, 900), 30)
    tags = rng.sample(range(100000, 990000), 24)     # prefijo "00": no se usa en TAGs reales
    flota = []
    for i in range(24):
        flota.append({"tag": f"00{tags[i]}", "patente": f"ZZ{numeros[i]}AA", "dueno": RESPONSABLES[i % 6],
                      "equipo": f"EQUIPO {101 + i}", "estado": "INACTIVO" if i == 23 else "ACTIVO",
                      "marca": MARCAS_TRACTOR[i % 4]})
    for j in range(6):                                # los acoplados no llevan TAG
        flota.append({"tag": "", "patente": f"ZZ{numeros[24 + j]}AC", "dueno": RESPONSABLES[j],
                      "equipo": f"ACOPLADO {j + 1:02d}", "estado": "ACTIVO", "marca": "SEMIRREMOLQUE"})
    return flota


def _facturas(rng, flota, meses):
    por_tag = {f["tag"]: f for f in flota if f["tag"]}
    conocidos = list(por_tag)
    pesos = [3 if i % 5 == 0 else 1 for i in range(len(conocidos))]   # algunos camiones andan mas que otros
    total = sum(PASES_POR_FACTURA)
    tags = [t for t, n in zip(TAGS_SIN_FLOTA, PASES_SIN_FLOTA) for _ in range(n)]
    tags += rng.choices(conocidos, weights=pesos, k=total - len(tags))
    rng.shuffle(tags)
    facturas, pos = [], 0
    for k, cant in enumerate(PASES_POR_FACTURA):
        emisor = ORDEN_EMISORES[k % 3]
        a, m = meses[k // 3]
        dias = _dias_del_mes(a, m)
        items = []
        for tag in tags[pos:pos + cant]:
            estacion, tarifa = rng.choice(EMISORES[emisor])
            items.append({"fecha": _fecha(date(a, m, rng.randint(1, dias))), "tag": tag,
                          "concepto": f"{estacion} - TELEPASE", "monto": tarifa})
        pos += cant
        items.sort(key=lambda it: (it["fecha"][6:], it["fecha"][3:5], it["fecha"][:2], it["tag"]))
        suma = round(sum(it["monto"] for it in items), 2)
        fecha_doc = _fecha(date(a, m, dias))
        declarado = round(suma + DIFERENCIA, 2) if k == FACTURA_CON_DIFERENCIA else suma
        nombre = f"EJEMPLO_{emisor}_{a}-{m:02d}_{k + 1}.pdf"
        lineas = [(f"Fecha: {fecha_doc}      Cliente: {NOMBRE_EMPRESA}", False), ("", False),
                  ("Cant Descripcion                     TAG          Fecha          Importe", True)]
        for it in items:
            lineas.append((f"{1:<5}{it['concepto']:<30}SI{it['tag']:<11}{it['fecha']:<12}{_ar(it['monto']):>12}", False))
        lineas += [("", False), (f"Total declarado: $ {_ar(declarado)}", True)]
        pdf = _pdf(f"Factura de peajes de ejemplo - {emisor}",
                   "Documento ficticio para la demostración de PeajeControl Go. Sin valor fiscal.", lineas,
                   "Documento de ejemplo - no es una factura real")
        facturas.append({"nombre": nombre, "emisor": emisor, "fecha_doc": fecha_doc, "total_doc": declarado,
                         "suma": suma, "items": items, "pdf": pdf, "huella": hashlib.sha256(pdf).hexdigest()})
    return facturas


def _resumenes(rng, meses):
    resumenes = []
    for k, (banco, titular) in enumerate(RESUMENES):
        a, m = meses[1 + k]                       # los dos ultimos meses con cierre el dia 25
        cierre = date(a, m, min(25, _dias_del_mes(a, m)))
        gastos = []
        for _ in range(GASTOS_POR_RESUMEN):
            concepto, cat, lo, hi = rng.choice(GASTOS_MODELO)
            dia = rng.randint(1, cierre.day)
            gastos.append({"fecha": _fecha(date(a, m, dia)), "concepto": concepto, "categoria": cat,
                           "monto": round(rng.randint(lo * 4, hi * 4) / 4.0, 2)})
        gastos.sort(key=lambda g: g["fecha"][:2])
        total = round(sum(g["monto"] for g in gastos), 2)
        venc = cierre + timedelta(days=12)
        nombre = f"EJEMPLO_resumen_tarjeta_{k + 1}_{a}-{m:02d}.pdf"
        lineas = [(f"Titular: {titular}      Cierre: {_fecha(cierre)}      Vencimiento: {_fecha(venc)}", False), ("", False),
                  ("Fecha       Concepto                                     Importe", True)]
        for g in gastos:
            lineas.append((f"{g['fecha']:<12}{g['concepto']:<38}{_ar(g['monto']):>14}", False))
        lineas += [("", False), (f"TOTAL A PAGAR  $ {_ar(total)}", True)]
        pdf = _pdf(f"Resumen de tarjeta de ejemplo - {banco}",
                   "Documento ficticio para la demostración de PeajeControl Go. Sin valor fiscal.", lineas,
                   "Documento de ejemplo - no es un resumen real")
        resumenes.append({"nombre": nombre, "banco": banco, "titular": titular, "periodo": _fecha(cierre),
                          "vencimiento": _fecha(venc), "total": total, "gastos": gastos, "pdf": pdf,
                          "huella": hashlib.sha256(pdf).hexdigest()})
    return resumenes


def generar(hoy=None):
    """Todo el juego de datos de ejemplo. Mismo `hoy` -> mismos datos y mismos bytes de PDF."""
    hoy = hoy or date.today()
    rng = random.Random(SEMILLA)
    meses = _ultimos_tres_meses(hoy)
    flota = _flota(rng)
    return {"empresa": NOMBRE_EMPRESA, "flota": flota, "facturas": _facturas(rng, flota, meses),
            "resumenes": _resumenes(rng, meses)}
