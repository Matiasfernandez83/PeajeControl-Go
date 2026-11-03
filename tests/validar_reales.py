# Valida el lector contra los archivos reales. Uso:
#   python3 tests/validar_reales.py ["/ruta/a/carpeta"]
# Imprime por PDF: emisor, pases, total declarado vs sumado y semaforo OK/DIF.
import glob, os, sys, tempfile
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "v.db"))
os.environ.setdefault("SECRET_KEY", "x"); os.environ.setdefault("ADMIN_PASSWORD", "x")
import app as A

CARPETA = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/Facturas Peajecontrol go"

def main():
    pdfs = sorted(glob.glob(os.path.join(CARPETA, "**", "*.pdf"), recursive=True))
    fallas = 0
    print(f"{'ARCHIVO':42} {'EMISOR':36} {'PASES':>5} {'DECLARADO':>14} {'SUMADO':>14}  SEMAFORO")
    for f in pdfs:
        n = os.path.basename(f)[:41]
        try:
            r = A.parsear_factura_peajes(f)
        except ValueError as e:
            print(f"{n:42} {'-':36} {'-':>5} {'-':>14} {'-':>14}  AVISO: {e}"); continue
        suma = round(sum(i["monto"] for i in r["items"]), 2)
        dif = abs(r["total_doc"] - suma)
        if not r["emisor"]: sem = "EMISOR NO RECONOCIDO"
        elif not r["items"]: sem = "SIN PASES"
        elif not r["total_doc"]: sem = "SIN TOTAL (no controlable)"
        elif dif < 1: sem = "OK"
        else: sem = f"DIF {dif:,.2f}"
        if sem != "OK": fallas += 1
        print(f"{n:42} {(r['emisor'] or '?'):36} {len(r['items']):>5} {r['total_doc']:>14,.2f} {suma:>14,.2f}  {sem}")
    print(f"\n{len(pdfs)} PDFs, {fallas} sin semaforo OK")

main()
