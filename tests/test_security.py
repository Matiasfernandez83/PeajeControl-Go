# Tests de seguridad + flujo completo. Corre con SQLite local: python3 tests/test_security.py
import io, os, re, sys, tempfile
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.pop("DATABASE_URL", None)
os.environ["ADMIN_PASSWORD"] = "clave-de-test-123"
os.environ["SECRET_KEY"] = "secret-de-test"
_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import app as A
from openpyxl import Workbook, load_workbook

EMAIL, CLAVE = "admin@peajecontrol.com", os.environ["ADMIN_PASSWORD"]
app = A.app; app.config["TESTING"] = False  # que los errores se vean como en prod

def cliente():
    return app.test_client()

def token(c, ruta="/login"):
    c.get(ruta)
    with c.session_transaction() as s:
        return s["_csrf"]

def login(c, email=EMAIL, clave=CLAVE):
    return c.post("/login", data={"email": email, "clave": clave, "_csrf": token(c)})

def logueado():
    A.INTENTOS.clear()
    c = cliente(); r = login(c)
    assert r.status_code == 302 and r.headers["Location"].endswith("/"), "login fallido"
    return c

def post(c, ruta, data=None, ref="/config"):
    d = dict(data or {}); d["_csrf"] = token(c, ref)
    return c.post(ruta, data=d, content_type="multipart/form-data")

def xlsx(filas):
    wb = Workbook(); ws = wb.active
    for f in filas: ws.append(f)
    b = io.BytesIO(); wb.save(b); b.seek(0); return b

# ---------------- tests ----------------
def test_rutas_exigen_login():
    c = cliente()
    for ruta in ["/", "/movimientos", "/importar", "/flota", "/gastos", "/reportes", "/config",
                 "/empresas", "/exportar", "/gastos/exportar", "/detalle/monto"]:
        r = c.get(ruta)
        assert r.status_code == 302 and "/login" in r.headers["Location"], f"{ruta} sin login: {r.status_code}"

def test_headers_seguridad():
    r = cliente().get("/login")
    h = r.headers
    assert h["X-Frame-Options"] == "DENY"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "no-referrer"
    csp = h["Content-Security-Policy"]
    for parte in ("default-src 'self'", "img-src 'self' data:", "script-src 'self' 'unsafe-inline'",
                  "style-src 'self' 'unsafe-inline'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"):
        assert parte in csp, f"falta en CSP: {parte}"
    assert "Strict-Transport-Security" not in h  # solo en produccion

def test_csrf():
    A.INTENTOS.clear()
    c = cliente(); c.get("/login")
    r = c.post("/login", data={"email": EMAIL, "clave": CLAVE})
    assert r.status_code == 400, "POST sin token debe dar 400"
    r = c.post("/login", data={"email": EMAIL, "clave": CLAVE, "_csrf": "falso"})
    assert r.status_code == 400, "POST con token falso debe dar 400"
    assert login(c).status_code == 302, "POST con token valido debe pasar"
    # ya logueado: una accion destructiva sin token tambien se bloquea
    assert c.post("/movimientos/borrar", data={"ids": "1"}).status_code == 400

def test_formularios_con_csrf():
    c = logueado()
    for ruta, n in [("/flota", 1), ("/importar", 1), ("/gastos", 1), ("/config", 1), ("/empresas", 1), ("/reportes", 0)]:
        html = c.get(ruta).get_data(as_text=True)
        assert html.count('name="_csrf"') >= n, f"{ruta}: faltan inputs _csrf"
    for f in os.listdir(os.path.join(RAIZ, "templates")):
        src = open(os.path.join(RAIZ, "templates", f), encoding="utf-8").read()
        assert len(re.findall(r'<form[^>]*method="post"', src)) == src.count('name="_csrf"'), f"{f}: form POST sin _csrf"

def test_rate_limit_login():
    A.INTENTOS.clear()
    c = cliente()
    for _ in range(5):
        r = login(c, "victima@x.com", "mala")
        assert r.status_code == 200
    r = login(c, "victima@x.com", "mala")
    assert r.status_code == 429, f"se esperaba 429, vino {r.status_code}"
    assert "Demasiados intentos" in r.get_data(as_text=True)
    assert login(c, "victima@x.com", CLAVE).status_code == 429  # sigue bloqueado aunque acierte
    A.INTENTOS.clear()

def test_login_exitoso_limpia_y_rota_sesion():
    A.INTENTOS.clear()
    c = cliente()
    for _ in range(3): login(c, EMAIL, "mala")
    old = token(c)
    assert login(c).status_code == 302
    with c.session_transaction() as s:
        assert s.permanent is True
        assert s.get("_csrf") != old, "la sesion debe renovarse al loguear"
    assert not A.INTENTOS.get("e:" + EMAIL), "el contador debe limpiarse"
    assert app.config["PERMANENT_SESSION_LIFETIME"].total_seconds() == 8 * 3600

def test_no_sqli():
    c = logueado()
    for q in ["'", "' OR 1=1 --", "\"; DROP TABLE movimientos; --"]:
        assert c.get("/movimientos", query_string={"q": q}).status_code == 200
        assert c.get("/movimientos", query_string={"dueno": q}).status_code == 200
        assert c.get("/detalle/dueno", query_string={"v": q}).status_code == 200
    assert c.get("/movimientos").status_code == 200  # la tabla sigue viva

def test_secret_key_sin_fallback():
    src = open(os.path.join(RAIZ, "app.py"), encoding="utf-8").read()
    assert "cambiar-en-produccion" not in src
    assert not re.search(r'environ\.get\(\s*"SECRET_KEY"\s*,', src), "SECRET_KEY no debe tener default"
    assert A.app.secret_key == "secret-de-test"

def test_admin_inicial_sin_clave_fija():
    src = open(os.path.join(RAIZ, "app.py"), encoding="utf-8").read()
    assert 'generate_password_hash("admin")' not in src
    u = A._conectar().execute("SELECT clave FROM usuarios WHERE email=?", (EMAIL,)).fetchone()
    assert A.check_password_hash(u["clave"], CLAVE) and not A.check_password_hash(u["clave"], "admin")

def test_xl_sanitiza():
    for peligro in ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", "\r=x"]:
        assert A.xl(peligro).startswith("'"), peligro
    assert A.xl("Peaje normal") == "Peaje normal" and A.xl(12.5) == 12.5 and A.xl("") == ""

def test_flujo_completo_y_export_sin_formulas():
    c = logueado()
    # flota
    r = post(c, "/flota", {"archivo": (xlsx([["TAG", "PATENTE", "RESPONSABLE", "EQUIPO"],
        ["1234567890123456", "AB123CD", "Juan Perez", "Camion 1"],
        ["=HYPERLINK(\"http://x\")9999", "ZZ999ZZ", "=cmd|' /C calc'!A0", "E2"]]), "flota.xlsx")}, "/flota")
    assert r.status_code == 302
    assert "AB123CD" in c.get("/flota").get_data(as_text=True)
    # importar movimientos (concepto malicioso incluido)
    r = post(c, "/importar", {"archivo": (xlsx([["FECHA", "TAG", "CONCEPTO", "IMPORTE"],
        ["01/10/2026", "1234567890123456", "Peaje Dellepiane", "1.500,00"],
        ["02/10/2026", "5555555555555555", "=HYPERLINK(\"http://evil\",\"x\")", "2.000,00"],
        ["03/10/2026", "5555555555555555", "@SUM(1+1)", "300,00"]]), "peajes.xlsx")}, "/importar")
    assert r.status_code == 302
    # tablero y detalle
    assert "Peaje" in c.get("/").get_data(as_text=True) or c.get("/").status_code == 200
    j = c.get("/detalle/monto").get_json()
    assert len(j["filas"]) == 3
    assert c.get("/detalle/duenos").status_code == 200
    assert c.get("/movimientos?vista=vehiculo").status_code == 200
    assert c.get("/movimientos?vista=responsable").status_code == 200
    assert c.get("/movimientos?filtro=sinmatch").status_code == 200
    # gastos
    r = post(c, "/gastos", {"archivo": (xlsx([["FECHA", "DESCRIPCION", "MONTO"],
        ["05/10/2026", "YPF Ruta 9", "10.000,00"], ["06/10/2026", "=1+1", "500,00"]]), "tarjeta.xlsx")}, "/gastos")
    assert r.status_code == 302
    assert "YPF" in c.get("/gastos").get_data(as_text=True)
    # reportes + descarga
    assert "peajes.xlsx" in c.get("/reportes").get_data(as_text=True)
    assert c.get("/reportes/descargar/peajes.xlsx").status_code == 200
    # exportar: ninguna celda de texto debe empezar con = + - @
    for ruta in ("/exportar", "/gastos/exportar"):
        r = c.get(ruta); assert r.status_code == 200
        wb = load_workbook(io.BytesIO(r.data))
        for ws in wb.worksheets:
            for fila in ws.iter_rows(values_only=True):
                for v in fila:
                    assert not (isinstance(v, str) and v[:1] in ("=", "+", "-", "@")), f"{ruta}: celda peligrosa {v!r}"
    # config: clave, usuario, borrar usuario
    assert c.get("/config").status_code == 200
    r = post(c, "/config/clave", {"nueva": CLAVE})
    assert r.status_code == 302
    r = post(c, "/config/usuario", {"email": "op@test.com", "clave": "operador1", "nombre": "Op"})
    assert r.status_code == 302 and "op@test.com" in c.get("/config").get_data(as_text=True)
    uid = A._conectar().execute("SELECT id FROM usuarios WHERE email='op@test.com'").fetchone()["id"]
    post(c, f"/config/usuario/borrar/{uid}")
    assert "op@test.com" not in c.get("/config").get_data(as_text=True)
    # empresas: crear, aislamiento, borrar
    post(c, "/empresas/crear", {"empresa": "Otra SA", "email": "otra@test.com", "clave": "otraotra", "nombre": "Dueño"}, "/empresas")
    assert "Otra SA" in c.get("/empresas").get_data(as_text=True)
    c2 = cliente(); assert login(c2, "otra@test.com", "otraotra").status_code == 302
    assert c2.get("/detalle/monto").get_json()["filas"] == [], "la otra empresa no debe ver datos ajenos"
    assert c2.get("/reportes/descargar/peajes.xlsx").status_code == 302
    assert c2.get("/empresas").status_code == 302  # no es superadmin
    eid = A._conectar().execute("SELECT id FROM empresas WHERE nombre='Otra SA'").fetchone()["id"]
    post(c, f"/empresas/borrar/{eid}", ref="/empresas")
    assert "Otra SA" not in c.get("/empresas").get_data(as_text=True)
    # borrar movimientos y reportes
    post(c, "/reportes/borrar", {"nombres": "peajes.xlsx"}, "/reportes")
    assert c.get("/detalle/monto").get_json()["filas"] == []
    # salir
    assert c.get("/salir").status_code == 302
    assert c.get("/").status_code == 302

def test_plantilla_flota():
    c = logueado()
    r = c.get("/flota/plantilla")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert "plantilla_flota.xlsx" in r.headers["Content-Disposition"]
    ws = load_workbook(io.BytesIO(r.data)).active
    assert [x.value for x in ws[1]] == ["NUMERO DE TAG", "PATENTE", "RESPONSABLE", "EQUIPO", "ESTADO", "MARCA"]
    assert ws.max_row == 3
    # la plantilla debe poder re-importarse tal cual
    assert post(c, "/flota", {"archivo": (io.BytesIO(r.data), "p.xlsx")}, "/flota").status_code == 302
    assert b"94281610" in c.get("/flota").data

def test_carga_multiple_peajes():
    c = logueado()
    tok = token(c, "/importar")
    def f(nombre, filas): return (xlsx(filas), nombre)
    data = {"_csrf": tok, "archivo": [
        f("multi_a.xlsx", [["FECHA", "TAG", "CONCEPTO", "IMPORTE"], ["01/10/2026", "1111111111111111", "Peaje A", "100,00"],
                           ["02/10/2026", "1111111111111111", "Peaje A2", "50,00"]]),
        (io.BytesIO(b"esto no es un pdf"), "roto.pdf"),          # falla, no debe cortar a los demas
        f("multi_b.xlsx", [["FECHA", "TAG", "CONCEPTO", "IMPORTE"], ["03/10/2026", "2222222222222222", "Peaje B", "70,00"]])]}
    r = c.post("/importar", data=data, content_type="multipart/form-data", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "multi_a.xlsx" in html and "2 pasadas" in html, "falta resumen de multi_a"
    assert "multi_b.xlsx" in html and "1 pasadas" in html, "falta resumen de multi_b"
    assert "roto.pdf" in html and "✖" in html, "falta el error del archivo roto"
    movs = c.get("/movimientos").get_data(as_text=True)
    assert "11111111" in movs and "22222222" in movs
    # un solo archivo sigue andando
    r = c.post("/importar", data={"_csrf": token(c, "/importar"), "archivo": f("solo.xlsx",
        [["FECHA", "TAG", "CONCEPTO", "IMPORTE"], ["04/10/2026", "3333333333333333", "Peaje C", "10,00"]])},
        content_type="multipart/form-data", follow_redirects=True)
    assert "solo.xlsx" in r.get_data(as_text=True) and "1 pasadas" in r.get_data(as_text=True)

def test_carga_multiple_tope():
    c = logueado()
    archivos = [(xlsx([["FECHA", "TAG", "CONCEPTO", "IMPORTE"], ["01/10/2026", "444444444444444%d" % (i % 10), "Tope", "1,00"]]), f"tope{i}.xlsx")
                for i in range(A.MAX_ARCHIVOS_CARGA + 2)]
    r = c.post("/importar", data={"_csrf": token(c, "/importar"), "archivo": archivos},
               content_type="multipart/form-data", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert f"tope{A.MAX_ARCHIVOS_CARGA - 1}.xlsx" in html and f"tope{A.MAX_ARCHIVOS_CARGA}.xlsx" not in html
    assert "quedaron sin procesar 2" in html

def test_carga_multiple_resumenes():
    c = logueado()
    def f(n): return (xlsx([["FECHA", "DESCRIPCION", "MONTO"], ["01/10/2026", "SHELL combustible", "1.000,00"]]), n)
    r = c.post("/gastos", data={"_csrf": token(c, "/gastos"), "archivo": [f("r1.xlsx"), f("r2.xlsx")]},
               content_type="multipart/form-data", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "r1.xlsx" in html and "r2.xlsx" in html and html.count("1 consumos") >= 2

if __name__ == "__main__":
    fallos = 0
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"OK    {nombre}")
            except Exception as e:
                fallos += 1; print(f"FALLA {nombre}: {type(e).__name__}: {e}")
    print("\nTODO VERDE" if not fallos else f"\n{fallos} test(s) fallaron")
    sys.exit(1 if fallos else 0)
