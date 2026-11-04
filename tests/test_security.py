# Tests de seguridad + flujo completo. SQLite: python3 tests/test_security.py  |  PostgreSQL: bash tests/run_pg_tests.sh
import io, os, re, sys
import _entorno   # elige SQLite o PostgreSQL (TEST_DATABASE_URL) y prepara las variables antes de importar app
RAIZ, _tmp = _entorno.RAIZ, _entorno.TMP

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
def _skip(motivo):
    if "PYTEST_CURRENT_TEST" in os.environ:
        import pytest; pytest.skip(motivo)
    print(f"  (salteado: {motivo})")

def test_rutas_exigen_login():
    c = cliente()
    for ruta in ["/", "/movimientos", "/importar", "/flota", "/gastos", "/reportes", "/config",
                 "/empresas", "/exportar", "/gastos/exportar", "/detalle/monto", "/ver/monto", "/archivo/1", "/factura/x.pdf"]:
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

def test_flota_abm_manual():
    c = logueado()
    r = post(c, "/flota/agregar", {"tag": "ar-1234567-8", "patente": "zz 999 aa", "dueno": "JUAN", "equipo": "EQ1", "estado": "ACTIVO", "marca": "IVECO"}, "/flota")
    cx = A._conectar()
    f = cx.execute("SELECT * FROM flota WHERE patente='ZZ999AA'").fetchone()
    assert f and f["tag"] == "12345678" and f["dueno"] == "JUAN" and f["empresa_id"] == 1
    # duplicado por TAG o patente: no se agrega
    post(c, "/flota/agregar", {"tag": "12345678", "patente": "OTRA1"}, "/flota")
    post(c, "/flota/agregar", {"tag": "", "patente": "ZZ-999-AA"}, "/flota")
    assert cx.execute("SELECT COUNT(*) n FROM flota WHERE tag='12345678' OR patente='ZZ999AA'").fetchone()["n"] == 1
    # vacio: no se agrega
    antes = cx.execute("SELECT COUNT(*) n FROM flota").fetchone()["n"]
    post(c, "/flota/agregar", {"dueno": "SOLO NOMBRE"}, "/flota")
    assert cx.execute("SELECT COUNT(*) n FROM flota").fetchone()["n"] == antes
    # la tabla muestra, busca y pagina
    assert "ZZ999AA" in c.get("/flota?q=juan").get_data(as_text=True)
    assert "ZZ999AA" not in c.get("/flota?q=noexiste").get_data(as_text=True)
    # edicion
    post(c, f"/flota/editar/{f['id']}", {"tag": "12345678", "patente": "ZZ999AA", "dueno": "PEDRO", "marca": "SCANIA"}, "/flota")
    g = cx.execute("SELECT * FROM flota WHERE id=?", (f["id"],)).fetchone()
    assert g["dueno"] == "PEDRO" and g["marca"] == "SCANIA"
    # otra empresa: no ve, no edita, no borra equipos ajenos
    post(c, "/empresas/crear", {"empresa": "Flota Ajena SA", "email": "ajena@test.com", "clave": "ajenaajena", "nombre": "Ajeno"}, "/empresas")
    c2 = cliente(); login(c2, "ajena@test.com", "ajenaajena")
    assert "ZZ999AA" not in c2.get("/flota").get_data(as_text=True)
    post(c2, f"/flota/editar/{f['id']}", {"tag": "12345678", "dueno": "HACKEADO"}, "/flota")
    post(c2, f"/flota/borrar/{f['id']}", {}, "/flota")
    h = cx.execute("SELECT * FROM flota WHERE id=?", (f["id"],)).fetchone()
    assert h and h["dueno"] == "PEDRO", "otra empresa toco un equipo ajeno"
    # el rol no admin no puede borrar (usuario operador de la misma empresa)
    cx.execute("INSERT INTO usuarios(empresa_id,email,clave,nombre,rol) VALUES(1,'op@test.com',?,?,?)",
               (A.generate_password_hash("operador1"), "Op", "operador")); cx.commit()
    c3 = cliente(); login(c3, "op@test.com", "operador1")
    post(c3, f"/flota/borrar/{f['id']}", {}, "/flota")
    assert cx.execute("SELECT 1 FROM flota WHERE id=?", (f["id"],)).fetchone(), "operador no debe borrar"
    # baja por admin
    post(c, f"/flota/borrar/{f['id']}", {}, "/flota")
    assert not cx.execute("SELECT 1 FROM flota WHERE id=?", (f["id"],)).fetchone()
    # la carga masiva sigue funcionando
    c.post("/flota", data={"_csrf": token(c, "/flota"), "archivo": (xlsx([["NUMERO DE TAG", "PATENTE"], ["11112222", "AAA111"]]), "f.xlsx")},
           content_type="multipart/form-data")
    assert cx.execute("SELECT COUNT(*) n FROM flota WHERE empresa_id=1").fetchone()["n"] == 1

def test_migracion_agrega_columnas_faltantes():
    # Caso produccion: tabla archivos vieja SIN total_doc -> migrar la completa y /importar anda.
    if A.USE_PG: return _skip("version SQLite; en PostgreSQL corre test_migracion_agrega_columnas_faltantes_postgres")
    import sqlite3
    cx = sqlite3.connect(os.path.join(_tmp, "vieja.db")); cx.row_factory = sqlite3.Row
    cx.execute("CREATE TABLE archivos(id INTEGER PRIMARY KEY, nombre TEXT, tipo TEXT)")
    cx.execute("INSERT INTO archivos(nombre,tipo) VALUES('a.pdf','Peajes')")
    cx.execute("CREATE TABLE movimientos(id INTEGER PRIMARY KEY, fecha TEXT, tag TEXT, monto REAL)")
    cx.execute("INSERT INTO movimientos(fecha,tag,monto) VALUES('01/01/2026','12345678',10)")
    for t in ("usuarios", "flota", "resumenes", "gastos"):
        cx.execute(f"CREATE TABLE {t}(id INTEGER PRIMARY KEY)")
    cx.commit()
    A.migrar(cx)
    assert "total_doc" in A._columnas(cx, "archivos")
    assert "archivo" in A._columnas(cx, "movimientos") and "dueno" in A._columnas(cx, "movimientos")
    assert cx.execute("SELECT total_doc FROM archivos").fetchone()["total_doc"] == 0
    cx.execute("UPDATE movimientos SET archivo='a.pdf', empresa_id=1"); cx.execute("UPDATE archivos SET empresa_id=1")
    r = cx.execute("""SELECT m.archivo, COUNT(*) n, SUM(m.monto) t, COALESCE(MAX(a.total_doc),0) td
        FROM movimientos m LEFT JOIN archivos a ON a.nombre=m.archivo AND a.empresa_id=m.empresa_id
        WHERE m.empresa_id=1 GROUP BY m.archivo""").fetchall()
    assert len(r) == 1 and r[0]["td"] == 0
    A.migrar(cx)  # idempotente
    cx.close()

def test_flota_importar_base_post():
    # Caso produccion: POST /flota (Actualizar base) debe responder bien y cargar responsable/equipo/estado/marca.
    c = logueado()
    filas = [["NUMERO DE TAG", "PATENTE", "RESPONSABLE", "EQUIPO", "ESTADO", "MARCA"]]
    for i in range(300):
        filas.append([f"9000{i:04d}", f"AB{i:03d}CD", "REQUIERE APROB GP", f"EQUIPO {i}", "ACTIVO", "IVECO"])
    filas.append(["90000000", "DUP", "OTRO", "X", "X", "X"])   # TAG repetido: se descarta
    r = post(c, "/flota", {"archivo": (xlsx(filas), "BASE.xlsx")}, "/flota")
    assert r.status_code == 302, r.status_code
    d = A._conectar()
    try:
        n = d.execute("SELECT COUNT(*) n FROM flota WHERE dueno='REQUIERE APROB GP' AND estado='ACTIVO' "
                      "AND marca='IVECO' AND equipo LIKE ?", ("EQUIPO %",)).fetchone()["n"]
        total = d.execute("SELECT COUNT(*) n FROM flota").fetchone()["n"]
    finally:
        d.close()
    assert n == total == 300, (n, total)
    assert c.get("/flota").status_code == 200
    # una segunda carga reemplaza la base (no duplica)
    post(c, "/flota", {"archivo": (xlsx(filas[:11]), "BASE2.xlsx")}, "/flota")
    d = A._conectar()
    try: assert d.execute("SELECT COUNT(*) n FROM flota").fetchone()["n"] == 10
    finally: d.close()

def test_trazabilidad_factura_pdf_y_detalle_paginado():
    import base64
    c = logueado()
    d = A._conectar()
    try:
        E = d.execute("SELECT empresa_id FROM usuarios WHERE email=?", (EMAIL,)).fetchone()["empresa_id"]
        pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
        d.execute("INSERT INTO archivos(empresa_id,nombre,tipo,total_doc,contenido,mime,creado) VALUES(?,?,?,?,?,?,?)",
                  (E, "FC-TRAZA.pdf", "Peajes", 0, base64.b64encode(pdf).decode(), "application/pdf", "01/01/2026"))
        aid = d.execute("SELECT id FROM archivos WHERE empresa_id=? AND nombre='FC-TRAZA.pdf'", (E,)).fetchone()["id"]
        filas = [(E, "01/10/2026", "11111111", "" if i % 2 else "TR123AA", "Peaje", 10.0, "", "", 0, "FC-TRAZA.pdf", "x")
                 for i in range(120)]
        d.executemany("""INSERT INTO movimientos(empresa_id,fecha,tag,patente,concepto,monto,dueno,equipo,verificado,archivo,creado)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", filas)
        d.commit()
    finally:
        d.close()
    # PDF inline, scopeado por empresa
    for ruta in (f"/archivo/{aid}", "/factura/FC-TRAZA.pdf"):
        r = c.get(ruta)
        assert r.status_code == 200 and r.mimetype == "application/pdf", ruta
        assert r.headers["Content-Disposition"].startswith("inline"), r.headers["Content-Disposition"]
        assert r.data == pdf
    assert c.get("/archivo/999999").status_code == 404
    assert cliente().get(f"/archivo/{aid}").status_code == 302   # sin login
    post(c, "/empresas/crear", {"empresa": "Ajena SA", "email": "ajena@test.com", "clave": "ajenaajena", "nombre": "A"}, "/empresas")
    c2 = cliente(); assert login(c2, "ajena@test.com", "ajenaajena").status_code == 302
    assert c2.get(f"/archivo/{aid}").status_code == 404
    assert c2.get("/factura/FC-TRAZA.pdf").status_code == 404
    h2 = c2.get("/ver/factura?v=FC-TRAZA.pdf").get_data(as_text=True)
    assert "Sin resultados" in h2 and "/archivo/" not in h2
    # cada movimiento expone su factura de origen (id del archivo)
    j = c.get("/detalle/factura?v=FC-TRAZA.pdf").get_json()
    assert len(j["filas"]) == 120 and all(f["archivo"] == "FC-TRAZA.pdf" and f["archivo_id"] == aid for f in j["filas"])
    assert j["pagina"].startswith("/ver/factura")
    # pagina standalone paginada, con link a la factura y filtros conservados
    h1 = c.get("/ver/factura?v=FC-TRAZA.pdf").get_data(as_text=True)
    assert f"/archivo/{aid}" in h1 and "Página 1 de 3" in h1 and "p=2" in h1
    h3 = c.get("/ver/factura?v=FC-TRAZA.pdf&p=3").get_data(as_text=True)
    assert "Página 3 de 3" in h3
    assert "Página 3 de 3" in c.get("/ver/factura?v=FC-TRAZA.pdf&p=999").get_data(as_text=True)
    assert c.get("/ver/factura?v=FC-TRAZA.pdf&p=abc").status_code == 200
    hq = c.get("/ver/factura?v=FC-TRAZA.pdf&q=TR123AA").get_data(as_text=True)
    assert "Página 1 de 2" in hq and "q=TR123AA" in hq
    # S/D: movimientos sin patente, con su factura
    hsd = c.get("/ver/vehiculo?v=S/D").get_data(as_text=True)
    assert "S/D" in hsd and "FC-TRAZA.pdf" in hsd and "Página 1 de 2" in hsd
    assert "TR123AA" in c.get("/ver/vehiculo?v=TR123AA").get_data(as_text=True)
    # tarjetas del tablero y agrupados
    for t in ("monto", "operaciones", "verificados", "sinmatch", "camiones", "duenos"):
        assert c.get(f"/ver/{t}").status_code == 200, t
    assert "TR123AA" in c.get("/ver/camiones").get_data(as_text=True)
    assert c.get("/ver/dueno?v=Sin asignar").status_code == 200
    assert c.get("/ver/inexistente").status_code == 404
    # movimientos paginado + columna origen
    hm = c.get("/movimientos").get_data(as_text=True)
    assert "Página 1 de" in hm and f"/archivo/{aid}" in hm
    assert "target=\"_blank\"" in c.get("/movimientos?vista=vehiculo").get_data(as_text=True)

# ---------------- tests especificos de PostgreSQL (se saltean en SQLite) ----------------
def test_backend_es_el_pedido():
    # Evita el peor error de esta bateria: creer que se probo PostgreSQL cuando se probo SQLite.
    assert A.USE_PG == bool(os.environ.get("TEST_DATABASE_URL")), "el backend usado no es el pedido"

def test_esquema_cubre_columnas_esperadas():
    # Toda columna que el codigo espera debe existir en la base real (en SQLite y en PostgreSQL).
    d = A._conectar()
    try:
        for tabla, cols in A._columnas_esperadas().items():
            faltan = {c for c, _ in cols} - A._columnas(d, tabla)
            assert not faltan, f"{tabla}: faltan columnas {faltan}"
    finally:
        d.close()

def test_migracion_agrega_columnas_faltantes_postgres():
    # Bug de produccion #1: archivos sin total_doc. CREATE TABLE IF NOT EXISTS no hace ALTER;
    # migrar() tiene que completar la tabla vieja en PostgreSQL real.
    if not A.USE_PG: return _skip("solo PostgreSQL")
    import psycopg2, psycopg2.extensions, secrets
    nombre = "vieja_" + secrets.token_hex(4)
    admin = psycopg2.connect(os.environ["DATABASE_URL"]); admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {nombre}")
    raw = psycopg2.connect(psycopg2.extensions.make_dsn(os.environ["DATABASE_URL"], dbname=nombre))
    try:
        cur = raw.cursor()
        cur.execute("CREATE TABLE archivos(id SERIAL PRIMARY KEY, nombre TEXT, tipo TEXT)")
        cur.execute("INSERT INTO archivos(nombre,tipo) VALUES('a.pdf','Peajes')")
        cur.execute("CREATE TABLE movimientos(id SERIAL PRIMARY KEY, fecha TEXT, tag TEXT, monto DOUBLE PRECISION)")
        cur.execute("INSERT INTO movimientos(fecha,tag,monto) VALUES('01/01/2026','12345678',10)")
        for t in ("usuarios", "flota", "resumenes", "gastos"):
            cur.execute(f"CREATE TABLE {t}(id SERIAL PRIMARY KEY)")
        raw.commit()
        con = A.PGConn.__new__(A.PGConn); con._conn = raw; con._bad = False   # PGConn sobre esta base, sin pool
        A.migrar(con)
        assert "total_doc" in A._columnas(con, "archivos"), "migrar no agrego archivos.total_doc"
        assert {"archivo", "dueno", "verificado"} <= A._columnas(con, "movimientos")
        assert {"empresa_id", "clave", "rol"} <= A._columnas(con, "usuarios")
        assert con.execute("SELECT total_doc FROM archivos").fetchone()["total_doc"] == 0
        assert con.execute("SELECT COUNT(*) n FROM movimientos").fetchone()["n"] == 1, "se perdieron datos"
        A.migrar(con)   # idempotente
        con.commit()
    finally:
        raw.close()
        admin.cursor().execute(f"DROP DATABASE IF EXISTS {nombre}")
        admin.close()

def test_porcentaje_literal_en_sql():
    # Trampa latente: un % escrito a mano en el SQL (LIKE '%x%') funciona en SQLite pero en PostgreSQL
    # (psycopg2) reventaba con IndexError. PGConn ahora lo blinda; vale para execute y executemany.
    d = A._conectar()
    try:
        d.execute("DELETE FROM gastos WHERE concepto LIKE 'ZZPCT%'")
        d.executemany("INSERT INTO gastos(concepto) VALUES(?)", [("ZZPCT 100% ok",), ("ZZPCT otro",)])
        n = d.execute("SELECT COUNT(*) n FROM gastos WHERE concepto LIKE '%ZZPCT%'").fetchone()["n"]
        assert n == 2, n
        n = d.execute("SELECT COUNT(*) n FROM gastos WHERE concepto LIKE '%ZZPCT%' AND concepto LIKE ?", ("%100%%",)).fetchone()["n"]
        assert n == 1, n   # % literal escrito a mano + parametro ? en la misma query
        d.executemany("UPDATE gastos SET concepto = concepto || ' 5%' WHERE concepto LIKE 'ZZPCT%' AND id > ?", [(0,)])
        assert d.execute("SELECT COUNT(*) n FROM gastos WHERE concepto LIKE '%5%'").fetchone()["n"] == 2
    finally:
        d.rollback()
        d.close()

def test_flota_carga_masiva_en_bloques_postgres():
    # Bug de produccion #2: executemany hacia 1 viaje por fila -> timeout del worker en POST /flota.
    # Con la base local no se nota la demora, asi que se cuenta: las filas deben viajar en bloques.
    if not A.USE_PG: return _skip("solo PostgreSQL")
    import psycopg2.extensions, time
    llamadas = []
    class Cuenta(psycopg2.extensions.cursor):
        def execute(self, *a, **k):
            llamadas.append(1); return super().execute(*a, **k)
        def executemany(self, *a, **k):
            raise AssertionError("executemany: 1 viaje por fila (era el bug de produccion)")
    pool = A._get_pool()
    original = pool.getconn
    def getconn(*a, **k):
        c = original(*a, **k); c.cursor_factory = Cuenta; return c
    pool.getconn = getconn
    N = 1500
    try:
        c = logueado()
        filas = [["NUMERO DE TAG", "PATENTE", "RESPONSABLE", "EQUIPO", "ESTADO", "MARCA"]]
        for i in range(N):
            filas.append([f"8000{i:04d}", f"BT{i:04d}", "RESP", f"EQ {i}", "ACTIVO", "IVECO"])
        t0 = time.time()
        r = post(c, "/flota", {"archivo": (xlsx(filas), "GRANDE.xlsx")}, "/flota")
        seg = time.time() - t0
    finally:
        pool.getconn = original
        for conn in list(pool._pool) + list(pool._used.values()):
            conn.cursor_factory = None
    assert r.status_code == 302, r.status_code
    d = A._conectar()
    try: assert d.execute("SELECT COUNT(*) n FROM flota").fetchone()["n"] == N
    finally: d.close()
    assert 1 <= len(llamadas) <= -(-N // 500) + 1, f"{len(llamadas)} envios para {N} filas: no va en bloques"
    assert seg < 20, f"cargar {N} equipos tardo {seg:.1f}s"

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
