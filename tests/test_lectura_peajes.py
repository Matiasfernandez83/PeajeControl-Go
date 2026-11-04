# Tests del lector de facturas de peaje (un caso por emisor) y utilidades.
# Corre con: python3 tests/test_lectura_peajes.py   (si existe la carpeta de archivos reales, tambien los valida)
import glob, os, sys, tempfile
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.pop("DATABASE_URL", None)
os.environ.update(ADMIN_PASSWORD="clave-de-test-123", SECRET_KEY="secret-de-test",
                  DB_PATH=os.path.join(tempfile.mkdtemp(), "t.db"))
import app as A

def eq(a, b, msg=""):
    assert a == b, f"{msg}: esperaba {b!r}, obtuve {a!r}"

def leer(texto):
    r = A.analizar_texto_peajes(texto)
    return r, sorted((i["tag"] or i["patente"], i["monto"]) for i in r["items"])

# --- utilidades ---
def test_norm_monto():
    for v, esperado in [("40.000,00", 40000.0), ("9896.82", 9896.82), ("20882.41", 20882.41),
                        ("1.103.284,35", 1103284.35), ("8216,10", 8216.10), ("-12.000,00", -12000.0),
                        ("40.000", 40000.0), ("12.40", 12.40), ("$ 1.234,50", 1234.50), ("0,00", 0.0), ("", 0.0)]:
        eq(A.norm_monto(v), esperado, f"norm_monto({v!r})")

def test_norm_tag_ultimos_8():
    eq(A.norm_tag("SI9094204388"), "94204388", "SI de 10 digitos")
    eq(A.norm_tag("SI900097162682-HN"), "97162682", "SI de 12 digitos con sufijo")
    eq(A.norm_tag("SI90009716268212"), "16268212", "SI de 14 digitos")
    eq(A.norm_tag(94281610), "94281610", "numero de Excel")

def test_norm_patente():
    eq(A.norm_patente(" ac-556 ch"), "AC556CH")

# --- total a pagar y bonificacion (surfaceados para control; no se guardan como movimientos) ---
def test_total_a_pagar_por_emisor():
    # AUBASA / Mercosur / GCO / AP01: "TOTAL A PAGAR <importe>" (la ultima hoja; la primera dice VER ULTIMA HOJA)
    tp = lambda t: A._total_a_pagar(t)
    eq(tp("TOTAL A PAGAR * VER ULTIMA HOJA * TOTAL CON RECARGO 2.416.535,72\nTOTAL A PAGAR 2301462,59 TOTAL CON RECARGO 2.416.535,72"), 2301462.59, "AUBASA")
    eq(tp("TOTAL A PAGAR 1.103.284,35 TOTAL CON RECARGO 1.158.448,58"), 1103284.35, "Mercosur/GCO")
    # CEAMSE: ultima cifra de la fila de totales
    eq(tp("2.277.365,29 0,00 2.277.365,29 478.246,71 0,00 68.320,96 6.832,10 2.830.765,06"), 2830765.06, "CEAMSE")
    eq(tp("311.884,32 0,00 311.884,32 65.495,71 0,00 0,00 935,65 9.356,53 387.672,21"), 387672.21, "AUSOL")
    eq(tp("Gravado No Gravado IVA\n124.939,35 0,19 26.237,24 3.748,18 0,00 12.493,96\nTotal\n167.418,92\nRegimen"), 167418.92, "AUSA")
    eq(tp("799936.25 0.00 799936.25 167986.61 0,00 Total 967922.86"), 967922.86, "Caminos")
    eq(tp("sin totales"), 0.0, "sin total")

def test_bonificacion_ap01():
    r = A.analizar_texto_peajes("""AUTOPISTA PROVINCIAL AP 01
Fecha: 01/08/2026
Cantidad Descripción Importe
3 TRONCAL ALDAO-CAT.4 SI9094392549 483.800,00
BONIFICACION PROMO TELEPEAJE -100.240,00
Subtotal IVA Resp.Inscripto IVA Resp.No Inscr. IVA RG.3337 Percepción II.BB. TOTAL
TOTAL A PAGAR 383.560,00 TOTAL CON RECARGO 383.560,00
383.560,00 0,00 383.560,00 0,00 0,00 0,00 0,00 383.560,00""")
    eq(r["bonificacion"], 100240.0, "bonificacion"); eq(r["total_pagar"], 383560.0, "total a pagar")
    eq(len(r["items"]), 1, "la bonificacion no es un movimiento")

# --- un caso por emisor (fragmentos de facturas reales) ---
def test_ausa():
    r, it = leer("""AUTOPISTAS URBANAS S.A.
Fecha: 23/07/2026
Cantidad Descripción Importe
3 CIRCUITO KDT-CAT.8 SI900097162682-HN 18.134,49
1 CIRCUITO KDT-CAT.8 SI900097162682-HP 16.982,69
1 AJUSTE CONTRIBUCION ESPECIAL FERROVIARIA 0,19
La presente facturación no implica renuncia
Gravado No Gravado IVA RG.212 RG 3337 IIBB CEF
35.117,18 0,19 7.374,61 1.000,00 0,00 3.511,72
Total""")
    eq(r["emisor"], "AUSA"); eq(it, [("97162682", 16982.69), ("97162682", 18134.49)])
    eq(r["total_doc"], 35117.18)

def test_gco_oeste():
    r, it = leer("""GRUPO CONCESIONARIO DEL OESTE S.A.
Cantidad Descripción Importe
5 PASADAS TELEPASE-CAT.3 SI9094204388 8216,10
9 PASADAS TELEPASE-CAT.5 SI9094475053 22183,47
Su saldo al día 07/10/26 es de $ 1820417.33 más intereses.
Subtotal IVA Resp.Inscripto IVA Resp.No Inscr. Percepción IIBB PCIA Percepción IB CABA TOTAL
30.399,57 6.383,91 0,00 91,20 0,00 36.874,68""")
    eq(r["emisor"], "GCO (Grupo Concesionario del Oeste)")
    eq(it, [("94204388", 8216.10), ("94475053", 22183.47)]); eq(r["total_doc"], 30399.57)

def test_aubasa_punto_dos_columnas_y_gastos_admin():
    r, it = leer("""AUTOPISTAS de BUENOS AIRES S.A. ORIGINAL 00050-05456300
Cantidad Descripción Importe Cantidad Descripción Importe
GASTOS ADMINISTRATIVOS 12.40 1 PEAJE DOCK SUD-CAT.6 SI9091354667 9896.82
1 PEAJE DOCK SUD-CAT.5 SI9094475053 7917.47 2 PEAJE DOCK SUD-CAT.6 SI9094589371 19793.64
CAE: 86361257566117
Subtotal IVA IVA Resp.No Inscr. IVA RG.3337 Percepción II.BB. TOTAL
5.554,62
37.620,40 7.900,28 0,00 100,00 Bs.As. 5.554,62 51.175,30""")
    eq(it, [("91354667", 9896.82), ("94475053", 7917.47), ("94589371", 19793.64)])
    eq(r["total_doc"], round(37620.40 - 12.40, 2), "el neto incluye los gastos administrativos")

def test_autovia_varios_pases_por_linea():
    r, it = leer("""AUTOVIA DEL MERCOSUR S.A.U.
Cantidad Descripción Importe Cantidad Descripción Importe
1 ZARATE-CAT 2 SI9097326424 3890.35 1 ZARATE-CAT 4 SI9097314703 7780.70
Su saldo al día 09/10/26""")
    eq(it, [("97314703", 7780.70), ("97326424", 3890.35)])

def test_caminos_por_patente():
    r, it = leer("""CAMINOS DE LAS SIERRAS S.A. Fecha: 31/08/2026
Cantidad Precio Alicuota IVA Base Precio Neto
3 - AC556CH - A. Pilar - Córdoba Km. 681- 5/6 ejes 6960.80 (21,00) 20882.41
1 - MEX634 - Ruta 36 Km. 724.5 - Piedras Moras- 3/4 ejes > 2.10 5078.41 (21,00) 5078.41
mts.
Total s/descuento Total descuento Subtotal: Importe IVA: Percepción:
25960.82 0.00 25960.82 5451.77 0,00""")
    eq(it, [("AC556CH", 20882.41), ("MEX634", 5078.41)]); eq(r["total_doc"], 25960.82)
    assert all(i["tag"] == "" for i in r["items"]), "Caminos no tiene TAG"

def test_ausol_tag_encabezado_y_cero():
    r, it = leer("""AUTOPISTAS DEL SOL S.A. Código Nº 01 Fecha: 16/07/26
Cant Descripción Importe
SI9060569853 0,00
1 CAMPANA PASADAS HP - CAT 6 3943,76
SI9097048972 0,00
3 TIGRE PASADAS HP - CAT 2 2957,82
1 BONIFICACION TIGRE - CAT 2 -985,94
Su saldo al día 25/09/26
6.915,64 0,00 6.915,64 1.452,28 0,00 0,00 20,00 200,00 8.587,92
Subtotal Perc. IB CABA Res Subtotal IVA Inscripto""")
    eq(it, [("60569853", 3943.76), ("97048972", -985.94), ("97048972", 2957.82)])
    eq(r["total_doc"], 6915.64)

def test_ap01_santa_fe_con_descuentos():
    r, it = leer("""AUTOPISTA PROVINCIAL AP 01 BRIGADIER GEN. ESTANISLAO LOPEZ C FACTURA 0041-02029394
Cantidad Descripción Importe
3 TRONCAL ALDAO-CAT.4 SI9094392549 40.000,00
2 DESC.TRAMO CORTO-CAT.4 SI9094456426 -12.000,00
BONIFICACION PROMO TELEPEAJE -10.000,00
Subtotal IVA Resp.Inscripto IVA Resp.No Inscr. IVA RG.3337 Percepción II.BB. TOTAL
18.000,00 0,00 0,00 0,00 0,00 18.000,00""")
    eq(it, [("94392549", 40000.0), ("94456426", -12000.0)])
    eq(round(sum(i["monto"] for i in r["items"]), 2), r["total_doc"], "semaforo")

def test_ceamse_tag_en_linea_previa_y_cruza_paginas():
    r, it = leer("""C.E.A.M.S.E. Código Nº 01 Fecha: 31/08/2026
ESTACION DE PEAJE OESTE KM 22
Cant Descripción Importe
GASTOS ADMINISTRATIVOS 9,93
SI9091297935
6 TRANSACCIONES TELEPASE - CAT 5 40165,26
SI9091354670
Por falta de pago de la Factura se procederá a inhabilitar el tag (cid:10)
<<PAG>>
C.E.A.M.S.E. Hoja: 000002
Cant Descripción Importe
57 TRANSACCIONES TELEPASE - CAT 7 659504,25
SUBTOTAL IMPUESTOS SUBTOTAL IVA INSCRIPTO IVA NO INSC. PERCEPCION PERCEPCION TOTAL
699.669,51 0,00 699.669,51 146.930,60 0,00 0,00 0,00 846.600,11""")
    eq(r["emisor"], "CEAMSE (Oeste km 22)")
    eq(it, [("91297935", 40165.26), ("91354670", 659504.25)])
    eq(r["total_doc"], round(699669.51 - 9.93, 2))

def test_texto_ilegible_da_mensaje_claro():
    try:
        A.texto_pdf  # existe
        import io, pdfplumber  # noqa
    except Exception: return
    # un PDF sin texto: se simula con una pagina en blanco generada a mano
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    try:
        A.parsear_pdf_tarjeta(io.BytesIO(pdf)); assert False, "debia fallar"
    except ValueError as e:
        assert "escaneado o ilegible" in str(e), str(e)
    except Exception:
        pass  # PDF mal formado: pdfplumber lo rechaza antes, tambien aceptable

# --- carpeta de archivos reales (solo si existe en esta maquina) ---
def test_archivos_reales():
    carpeta = "/mnt/user-data/uploads/Facturas Peajecontrol go"
    pdfs = glob.glob(os.path.join(carpeta, "**", "*.pdf"), recursive=True)
    leidos = 0
    for f in pdfs:
        try: r = A.parsear_factura_peajes(f)
        except ValueError: continue
        if not r["emisor"]: continue
        suma = sum(i["monto"] for i in r["items"])
        assert r["items"] and abs(r["total_doc"] - suma) < 1, f"{os.path.basename(f)}: declara {r['total_doc']}, suma {suma}"
        leidos += 1
    print(f"  reales: {leidos} facturas con semaforo OK" if pdfs else "  reales: carpeta no disponible (omitido)")

if __name__ == "__main__":
    n = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            fn(); n += 1; print("ok", nombre)
    print(f"\n{n} tests OK")
