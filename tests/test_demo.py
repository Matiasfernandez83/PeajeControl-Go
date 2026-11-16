# Tests de la demo online: datos de ejemplo (sembrar_demo) y modo demo de SOLO LECTURA.
# SQLite: python3 -m pytest tests -q   |   PostgreSQL: bash tests/run_pg_tests.sh
import base64, hashlib, io, os, re, time
from urllib.parse import parse_qs, urlparse
from datetime import date
import pytest
import _entorno   # elige SQLite o PostgreSQL y prepara las variables antes de importar app

import app as A
import demo_datos
from flask import session

app = A.app; app.config["TESTING"] = False
HOY = date(2026, 11, 16)
TABLAS = ("empresas", "usuarios", "flota", "movimientos", "archivos", "resumenes", "gastos")
ADMIN, CLAVE = "admin@peajecontrol.com", os.environ["ADMIN_PASSWORD"]


# ---------------- ayudas ----------------
def quitar_demo():
    con = A._conectar()
    for e in con.execute("SELECT id FROM empresas WHERE demo=1").fetchall():
        for t in A.TABLAS_TENANT:
            con.execute(f"DELETE FROM {t} WHERE empresa_id=?", (e["id"],))
        con.execute("DELETE FROM empresas WHERE id=?", (e["id"],))
    con.commit(); con.close()

def consulta(sql, args=()):
    con = A._conectar()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()

def uno(sql, args=()):
    return consulta(sql, args)[0]

def foto_db():
    """Huella de TODO el contenido de las tablas: si algo se inserta, borra o cambia, cambia."""
    con = A._conectar(); h = hashlib.sha256()
    try:
        for t in TABLAS:
            for r in con.execute(f"SELECT * FROM {t} ORDER BY id").fetchall():
                h.update(repr(tuple(dict(r).values())).encode())
    finally:
        con.close()
    return h.hexdigest()

def conteos():
    return {t: uno(f"SELECT COUNT(*) n FROM {t}")["n"] for t in TABLAS}

def token(c, ruta="/demo"):
    with c.session_transaction() as s:
        return s.setdefault("_csrf", "token-de-test")

def cliente_demo(ip=None):
    c = app.test_client()
    r = c.get("/demo", headers={"X-Forwarded-For": ip} if ip else {})
    assert r.status_code == 302, r.status_code
    return c

def login_admin():
    A.INTENTOS.clear()
    c = app.test_client(); c.get("/login")
    with c.session_transaction() as s:
        tk = s["_csrf"]
    r = c.post("/login", data={"email": ADMIN, "clave": CLAVE, "_csrf": tk})
    assert r.status_code == 302
    return c

@pytest.fixture
def demo():
    A.USOS_DEMO.clear(); A.INTENTOS.clear()
    quitar_demo()
    assert A.sembrar_demo(hoy=HOY) is True
    yield uno("SELECT id FROM empresas WHERE demo=1")["id"]
    quitar_demo()


# ---------------- PASO 2: datos de ejemplo ----------------
def test_siembra_cantidades_y_semaforo(demo):
    E = demo
    emp = uno("SELECT nombre, demo FROM empresas WHERE id=?", (E,))
    assert emp["nombre"] == "Logística Ejemplo SRL" and emp["demo"] == 1
    flota = consulta("SELECT * FROM flota WHERE empresa_id=?", (E,))
    assert len(flota) == 30
    assert sum(1 for f in flota if f["tag"]) == 24 and sum(1 for f in flota if not f["tag"]) == 6
    assert len({f["dueno"] for f in flota}) == 6
    movs = uno("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=?", (E,))["n"]
    assert movs == 600
    archivos = consulta("SELECT nombre, tipo FROM archivos WHERE empresa_id=?", (E,))
    assert sorted(a["tipo"] for a in archivos) == ["Peajes"] * 9 + ["Resumen tarjeta"] * 2
    emisores = {n for a in archivos if a["tipo"] == "Peajes" for n in ("AUSA", "AUSOL", "AUBASA") if n in a["nombre"]}
    assert emisores == {"AUSA", "AUSOL", "AUBASA"}
    assert uno("SELECT COUNT(*) n FROM resumenes WHERE empresa_id=?", (E,))["n"] == 2
    assert uno("SELECT COUNT(*) n FROM gastos WHERE empresa_id=?", (E,))["n"] == 80

    # semaforo: 8 facturas OK y exactamente 1 con diferencia (lo mismo que calcula la pantalla Importar)
    filas = consulta("""SELECT m.archivo, SUM(m.monto) t, MAX(a.total_doc) td FROM movimientos m
        JOIN archivos a ON a.nombre=m.archivo AND a.empresa_id=m.empresa_id WHERE m.empresa_id=? GROUP BY m.archivo""", (E,))
    assert len(filas) == 9
    con_dif = [f for f in filas if abs(f["td"] - f["t"]) >= 1]
    assert len(con_dif) == 1 and abs(abs(con_dif[0]["td"] - con_dif[0]["t"]) - 12480) < 0.01

    # TAGs sin match: 2 TAGs distintos que no estan en la flota
    sin = consulta("SELECT DISTINCT tag FROM movimientos WHERE empresa_id=? AND verificado=0", (E,))
    assert len(sin) == 2
    en_flota = {f["tag"] for f in flota}
    assert all(s["tag"] not in en_flota for s in sin)
    assert uno("SELECT COUNT(*) n FROM movimientos WHERE empresa_id=? AND verificado=0", (E,))["n"] == 12

def test_siembra_sin_datos_reales(demo):
    E = demo
    patentes = [f["patente"] for f in consulta("SELECT patente FROM flota WHERE empresa_id=?", (E,))]
    assert all(re.fullmatch(r"ZZ\d{3}(AA|AC)", p) for p in patentes), patentes   # prefijo ZZ: no existe en la realidad
    tags = [f["tag"] for f in consulta("SELECT tag FROM flota WHERE empresa_id=? AND tag<>''", (E,))]
    tags += [m["tag"] for m in consulta("SELECT DISTINCT tag FROM movimientos WHERE empresa_id=?", (E,))]
    assert all(re.fullmatch(r"00\d{6}", t) for t in tags), tags                  # prefijo 00: no se usa en TAGs reales
    # ninguna palabra de la empresa real en ningun texto cargado (tablas ni PDFs)
    textos = []
    for t in ("flota", "movimientos", "archivos", "resumenes", "gastos"):
        for r in consulta(f"SELECT * FROM {t} WHERE empresa_id=?", (E,)):
            for k, v in r.items():
                textos.append(base64.b64decode(v).decode("latin-1") if k == "contenido" and v else str(v))
    junto = "\n".join(textos).lower()
    for palabra in ("furlong", "matias", "matías", "fernandez", "gmail", "peajecontrol.com"):
        assert palabra not in junto, palabra

def test_siembra_pdfs_marcados_y_coherentes(demo):
    for a in consulta("SELECT nombre, contenido, huella, mime FROM archivos WHERE empresa_id=?", (demo,)):
        pdf = base64.b64decode(a["contenido"])
        assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF"), a["nombre"]
        assert b"EJEMPLO" in pdf and b"V\xc1LIDO" in pdf, a["nombre"]          # marca visible "EJEMPLO - NO VALIDO"
        assert hashlib.sha256(pdf).hexdigest() == a["huella"] and a["mime"] == "application/pdf"
    import pdfplumber
    pdf = base64.b64decode(uno("SELECT contenido FROM archivos WHERE empresa_id=? AND tipo='Peajes' ORDER BY id", (demo,))["contenido"])
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        assert "EJEMPLO" in p.pages[0].extract_text()

def test_siembra_categorias_coinciden_con_la_app(demo):
    for g in consulta("SELECT concepto, categoria FROM gastos WHERE empresa_id=?", (demo,)):
        assert A.categorizar(g["concepto"]) == g["categoria"], g

def test_siembra_idempotente_y_no_toca_otras_empresas(demo):
    antes, hash_antes = conteos(), foto_db()
    assert A.sembrar_demo(hoy=HOY) is False
    assert conteos() == antes and foto_db() == hash_antes               # correrla de nuevo no duplica ni cambia nada
    assert uno("SELECT COUNT(*) n FROM empresas WHERE demo=1")["n"] == 1
    # reiniciar: vuelve a dejar exactamente lo mismo y no toca los datos de las demas empresas
    con = A._conectar(); otra = con.execute("SELECT id FROM empresas WHERE COALESCE(demo,0)=0 ORDER BY id LIMIT 1").fetchone()["id"]
    con.execute("INSERT INTO flota(empresa_id,tag,patente,dueno) VALUES(?,?,?,?)", (otra, "11112222", "AB123CD", "Marcador"))
    con.commit(); con.close()
    try:
        assert A.sembrar_demo(reiniciar=True, hoy=HOY) is True
        assert uno("SELECT COUNT(*) n FROM empresas WHERE demo=1")["n"] == 1
        assert {k: v for k, v in conteos().items() if k != "flota"} == {k: v for k, v in antes.items() if k != "flota"}
        assert uno("SELECT COUNT(*) n FROM flota WHERE patente='AB123CD'")["n"] == 1
    finally:
        con = A._conectar(); con.execute("DELETE FROM flota WHERE patente='AB123CD'"); con.commit(); con.close()

def test_generador_es_determinista():
    a, b = demo_datos.generar(HOY), demo_datos.generar(HOY)
    assert a["flota"] == b["flota"]
    assert [f["huella"] for f in a["facturas"]] == [f["huella"] for f in b["facturas"]]

def test_migracion_agrega_columna_demo():
    quitar_demo()
    con = A._conectar()
    try:
        if "demo" in A._columnas(con, "empresas"):
            con.execute("ALTER TABLE empresas DROP COLUMN demo"); con.commit()
        assert "demo" not in A._columnas(con, "empresas")
        A.migrar(con); con.commit()
        assert "demo" in A._columnas(con, "empresas")
        A.migrar(con); con.commit()                                            # idempotente
    finally:
        con.close()
    A.init_db()                                                                # y el arranque sigue andando
    assert uno("SELECT COUNT(*) n FROM empresas WHERE COALESCE(demo,0)=0")["n"] >= 1


# ---------------- PASO 3: modo demo ----------------
RUTAS_LECTURA = ["/", "/flota", "/movimientos", "/movimientos?vista=vehiculo", "/movimientos?vista=responsable",
                 "/movimientos?filtro=sinmatch", "/importar", "/gastos", "/gastos?cat=PEAJE", "/reportes",
                 "/ver/monto", "/ver/sinmatch", "/ver/camiones", "/ver/duenos", "/exportar", "/gastos/exportar",
                 "/static/css/app.css"]

def test_demo_entra_sin_registrarse_y_recorre_pantallas(demo):
    c = app.test_client()
    r = c.get("/demo")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    with c.session_transaction() as s:
        assert s["rol"] == "demo" and s["empresa_id"] == demo
        assert 1790 < s["demo_hasta"] - time.time() <= 1800                     # dura 30 minutos
    for ruta in RUTAS_LECTURA:
        A.USOS_DEMO.clear()
        r = c.get(ruta)
        assert r.status_code == 200, (ruta, r.status_code)
    nombre = uno("SELECT nombre FROM archivos WHERE empresa_id=? AND tipo='Peajes' ORDER BY id", (demo,))["nombre"]
    aid = uno("SELECT id FROM archivos WHERE empresa_id=? AND tipo='Peajes' ORDER BY id", (demo,))["id"]
    for ruta in (f"/archivo/{aid}", f"/factura/{nombre}", f"/ver/factura?v={nombre}", f"/reportes/descargar/{nombre}"):
        assert c.get(ruta).status_code == 200, ruta
    assert c.get(f"/archivo/{aid}").data.startswith(b"%PDF")

def test_demo_muestra_franja_y_oculta_botones(demo):
    c = cliente_demo()
    for ruta in ("/", "/flota", "/movimientos", "/importar", "/gastos", "/reportes"):
        html = c.get(ruta).get_data(as_text=True)
        assert "Estás en una demo con datos ficticios" in html and "Pedir mi prueba" in html, ruta
        assert 'href="/landing#contacto"' in html
        assert 'type="file"' not in html, ruta                                    # nada de subir
        assert "/movimientos/borrar" not in html and "/reportes/borrar" not in html, ruta
        for prohibido in ("Eliminar", "Agregar equipo", "Iniciar procesamiento", "Iniciar carga", 'name="ids"', 'name="nombres"',
                          "Editar equipo", 'href="/config"'):
            assert prohibido not in html, (ruta, prohibido)
    # y un usuario normal NO ve la franja
    a = login_admin()
    assert "demo con datos ficticios" not in a.get("/").get_data(as_text=True)
    assert 'type="file"' in a.get("/importar").get_data(as_text=True)

def test_demo_bloquea_todo_lo_que_no_es_lectura(demo):
    """Recorre TODAS las rutas registradas con todos los metodos: en demo la base queda idéntica y
    lo que no esta en la lista blanca (o no es GET/HEAD) nunca responde 200."""
    c = cliente_demo()
    tk = token(c)
    flota_id = uno("SELECT id FROM flota WHERE empresa_id=? ORDER BY id", (demo,))["id"]
    res_id = uno("SELECT id FROM resumenes WHERE empresa_id=?", (demo,))["id"]
    arch = uno("SELECT id, nombre FROM archivos WHERE empresa_id=? AND tipo='Peajes' ORDER BY id", (demo,))
    admin_id = uno("SELECT id FROM usuarios WHERE email=?", (ADMIN,))["id"]
    otra = uno("SELECT id FROM empresas WHERE COALESCE(demo,0)=0 ORDER BY id")["id"]
    valores = {"fid": flota_id, "rid": res_id, "aid": arch["id"], "uid": admin_id, "eid": otra,
               "nombre": arch["nombre"], "tipo": "monto", "filename": "css/app.css"}

    def url_de(rule):
        def sub(m):
            return str(valores.get(m.group(2), "1" if m.group(1) == "int" else "x"))
        return re.sub(r"<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>", sub, rule.rule)

    def datos():
        return {"_csrf": tk, "empresa": "Pirata SA", "email": "pirata@x.com", "clave": "123456", "nombre": "Pirata",
                "nueva": "hackeada1", "tag": "00000001", "patente": "ZZ999ZZ", "dueno": "X", "equipo": "X", "estado": "X",
                "marca": "X", "ids": [flota_id], "archivo": (io.BytesIO(b"FECHA;MONTO\n01/01/2026;100,00\n"), "malo.csv"),
                "nombres": arch["nombre"]}

    antes, conteo_antes = foto_db(), conteos()
    pedidos = 0
    for rule in app.url_map.iter_rules():
        for metodo in ("GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            A.USOS_DEMO.clear()
            url = url_de(rule)
            kw = {"data": datos(), "content_type": "multipart/form-data"} if metodo in ("POST", "PUT", "DELETE", "PATCH") else {}
            r = getattr(c, metodo.lower())(url, headers={"Referer": "http://localhost/flota"}, **kw)
            pedidos += 1
            if rule.endpoint == "salir" and metodo in ("GET", "HEAD"):                  # salir cierra la sesion: se vuelve a entrar a la demo
                assert c.get("/demo").status_code == 302; tk = token(c)
            assert r.status_code < 500, (metodo, url, r.status_code)
            es_lectura_permitida = metodo in ("GET", "HEAD") and rule.endpoint in A.DEMO_PERMITIDAS
            if not es_lectura_permitida:
                assert r.status_code == 303, (metodo, url, r.status_code)       # redirección, nunca 200
            assert conteos() == conteo_antes, (metodo, url)
    assert pedidos > 100
    assert foto_db() == antes                                                  # nada se inserto, borro ni cambio
    # el aviso se ve al volver
    r = c.post("/flota/agregar", data=datos(), headers={"Referer": "http://localhost/flota"})
    assert r.headers["Location"] == "/flota"
    assert "En la demo no se modifica nada. ¿Querés probarlo con tus facturas?" in c.get("/flota").get_data(as_text=True)
    # sin token CSRF tambien: bloqueo prolijo (no un 400)
    r = c.post("/movimientos/borrar", data={"ids": [1]})
    assert r.status_code == 303

def test_lista_blanca_solo_tiene_rutas_que_existen():
    existentes = {r.endpoint for r in app.url_map.iter_rules()}
    assert A.DEMO_PERMITIDAS <= existentes, A.DEMO_PERMITIDAS - existentes
    assert not {"login", "config", "empresas", "crear_empresa", "crear_usuario", "cambiar_clave", "flota_agregar",
                "borrar_movimientos", "borrar_resumen", "borrar_archivo", "flota_plantilla"} & A.DEMO_PERMITIDAS

def test_demo_la_base_se_abre_en_solo_lectura(demo):
    """2da barrera: aunque una ruta se colara, la conexion de un request demo no puede escribir."""
    antes = foto_db()
    with app.test_request_context("/"):
        session["rol"] = "demo"
        d = A.db()
        with pytest.raises(Exception):
            d.execute("INSERT INTO flota(empresa_id,tag,patente) VALUES(?,?,?)", (demo, "00000009", "ZZ000ZZ"))
        with pytest.raises(Exception):
            d.execute("DELETE FROM movimientos");
        d.rollback()
    assert foto_db() == antes
    with app.test_request_context("/"):   # y un usuario normal si escribe (la conexion no quedo marcada en el pool)
        session["rol"] = "admin"
        d = A.db()
        d.execute("INSERT INTO flota(empresa_id,tag,patente) VALUES(?,?,?)", (demo, "00000009", "ZZ000ZZ"))
        d.execute("DELETE FROM flota WHERE patente='ZZ000ZZ'"); d.commit()

def test_demo_no_ve_datos_de_otra_empresa(demo):
    con = A._conectar()
    otra = con.execute("INSERT INTO empresas(nombre,creado) VALUES(?,?) RETURNING id", ("Empresa Ajena SA", "01/01/2026")).fetchone()["id"]
    pdf = b"%PDF-1.4 SECRETO-AJENO"
    ajeno = con.execute("""INSERT INTO archivos(empresa_id,nombre,tipo,total_doc,contenido,mime,creado,huella)
        VALUES(?,?,?,?,?,?,?,?) RETURNING id""", (otra, "secreto_ajeno.pdf", "Peajes", 777.0, base64.b64encode(pdf).decode(),
        "application/pdf", "01/01/2026 10:00", "h")).fetchone()["id"]
    con.execute("INSERT INTO flota(empresa_id,tag,patente,dueno) VALUES(?,?,?,?)", (otra, "55556666", "QQ111QQ", "Duena Ajena"))
    con.execute("""INSERT INTO movimientos(empresa_id,fecha,tag,patente,concepto,monto,dueno,verificado,archivo)
        VALUES(?,?,?,?,?,?,?,?,?)""", (otra, "01/01/2026", "55556666", "QQ111QQ", "PEAJE AJENO", 1234.0, "Duena Ajena", 1, "secreto_ajeno.pdf"))
    rid = con.execute("""INSERT INTO resumenes(empresa_id,archivo,banco,titular,total_resumen) VALUES(?,?,?,?,?) RETURNING id""",
                      (otra, "resumen_ajeno.pdf", "BancoAjeno", "Titular Ajeno", 5.0)).fetchone()["id"]
    con.execute("INSERT INTO gastos(empresa_id,resumen_id,fecha,concepto,categoria,monto) VALUES(?,?,?,?,?,?)",
                (otra, rid, "01/01/2026", "GASTO AJENO", "GASTO", 5.0))
    con.commit(); con.close()
    try:
        c = cliente_demo()
        propios = {r["id"] for r in consulta("SELECT id FROM archivos WHERE empresa_id=?", (demo,))}
        for i in range(0, max(propios | {ajeno}) + 10):                            # probar TODOS los ids cercanos
            r = c.get(f"/archivo/{i}")
            assert (r.status_code == 200) == (i in propios), (i, r.status_code)
            assert b"SECRETO-AJENO" not in r.data
        assert c.get("/archivo/%d" % ajeno).status_code == 404
        assert c.get("/factura/secreto_ajeno.pdf").status_code == 404
        r = c.get("/reportes/descargar/secreto_ajeno.pdf")
        assert r.status_code == 302 and b"SECRETO-AJENO" not in r.data
        ajenos = ("secreto_ajeno", "QQ111QQ", "55556666", "Duena Ajena", "PEAJE AJENO", "BancoAjeno", "GASTO AJENO", "Empresa Ajena")
        for ruta in ("/", "/flota?q=QQ111QQ", "/flota?q=Duena", "/movimientos?q=QQ111QQ", "/movimientos?q=AJENO", "/movimientos?dueno=Duena%20Ajena",
                     "/movimientos?vista=vehiculo", "/movimientos?vista=responsable", "/ver/vehiculo?v=QQ111QQ", "/ver/dueno?v=Duena%20Ajena",
                     "/ver/factura?v=secreto_ajeno.pdf", "/ver/operaciones?q=AJENO", "/ver/camiones", "/ver/duenos", "/gastos", "/gastos?cat=GASTO",
                     "/reportes", "/importar", f"/?empresa_id={otra}", f"/flota?empresa_id={otra}"):
            html = c.get(ruta).get_data(as_text=True)
            for eco in parse_qs(urlparse(ruta).query).values():                 # lo que el propio visitante escribio se repite en la pagina
                html = html.replace(eco[0], "")
            for x in ajenos:
                assert x not in html, (ruta, x)
        for ruta in ("/exportar", "/gastos/exportar"):
            A.USOS_DEMO.clear()
            assert b"AJENO" not in c.get(ruta).data
        # intentar cambiar de empresa por la cookie no sirve: esta firmada
        with c.session_transaction() as s:
            assert s["empresa_id"] == demo
    finally:
        con = A._conectar()
        for t in A.TABLAS_TENANT:
            con.execute(f"DELETE FROM {t} WHERE empresa_id=?", (otra,))
        con.execute("DELETE FROM empresas WHERE id=?", (otra,)); con.commit(); con.close()

def test_usuario_demo_no_entra_por_el_login(demo):
    from werkzeug.security import generate_password_hash
    A.INTENTOS.clear()
    c = app.test_client(); c.get("/login")
    with c.session_transaction() as s:
        tk = s["_csrf"]
    for email in ("demo", "demo@peajecontrol.com", "demo@demo.com", ""):
        r = c.post("/login", data={"email": email, "clave": "demo", "_csrf": tk})
        assert r.status_code == 200, email
        with c.session_transaction() as s:
            assert "uid" not in s
    # aunque alguien creara un usuario con rol "demo" y clave conocida, el formulario lo rechaza
    con = A._conectar()
    con.execute("INSERT INTO usuarios(empresa_id,email,clave,nombre,rol) VALUES(?,?,?,?,?)",
                (demo, "falso-demo@x.com", generate_password_hash("clave-conocida"), "Falso", "demo"))
    con.commit(); con.close()
    try:
        A.INTENTOS.clear()
        r = c.post("/login", data={"email": "falso-demo@x.com", "clave": "clave-conocida", "_csrf": tk})
        assert r.status_code == 200
        with c.session_transaction() as s:
            assert "uid" not in s
    finally:
        con = A._conectar(); con.execute("DELETE FROM usuarios WHERE email='falso-demo@x.com'"); con.commit(); con.close()

def test_demo_vence_a_los_30_minutos(demo):
    c = cliente_demo()
    assert c.get("/flota").status_code == 200
    with c.session_transaction() as s:
        s["demo_hasta"] = time.time() - 1                                         # ya paso la media hora
    r = c.get("/flota")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    with c.session_transaction() as s:
        assert "uid" not in s and "rol" not in s and "empresa_id" not in s        # sesion cerrada
    r = c.get("/flota")                                                           # y no vuelve a entrar sin /demo
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    # una cookie vieja sin fecha tampoco sirve
    c2 = cliente_demo()
    with c2.session_transaction() as s:
        s.pop("demo_hasta")
    assert c2.get("/flota").status_code == 302

def test_demo_limite_de_ingresos_por_ip(demo):
    A.USOS_DEMO.clear()
    for _ in range(20):
        assert app.test_client().get("/demo", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 302
    r = app.test_client().get("/demo", headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 429
    r = app.test_client().get("/demo", headers={"X-Forwarded-For": "falsa.1.1.1, 9.9.9.9"})   # cuenta la IP que agrega Render (la ultima)
    assert r.status_code == 429
    assert app.test_client().get("/demo", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 302   # otra IP no se ve afectada
    A.USOS_DEMO.clear()

def test_demo_limite_de_exportaciones_excel(demo):
    A.USOS_DEMO.clear()
    c = cliente_demo(ip="7.7.7.7")
    h = {"X-Forwarded-For": "7.7.7.7"}
    for i in range(5):
        assert c.get("/exportar" if i % 2 else "/gastos/exportar", headers=h).status_code == 200
    r = c.get("/exportar", headers=h)
    assert r.status_code == 303                                                   # la 6ta se corta
    assert c.get("/flota", headers=h).status_code == 200                          # el resto de la demo sigue andando
    assert c.get("/exportar", headers={"X-Forwarded-For": "6.6.6.6"}).status_code == 200   # otra IP, otro cupo
    # un usuario normal no tiene este limite
    a = login_admin()
    for _ in range(8):
        assert a.get("/exportar", headers=h).status_code == 200
    A.USOS_DEMO.clear()

def test_usuario_normal_no_se_ve_afectado(demo):
    a = login_admin()
    for ruta in ("/", "/flota", "/movimientos", "/importar", "/gastos", "/reportes", "/config", "/empresas", "/exportar"):
        assert a.get(ruta).status_code == 200, ruta
    with a.session_transaction() as s:
        tk = s["_csrf"]
    n = uno("SELECT COUNT(*) n FROM flota")["n"]
    r = a.post("/flota/agregar", data={"_csrf": tk, "tag": "12345678", "patente": "AA111AA", "dueno": "Real"})
    assert r.status_code == 302 and uno("SELECT COUNT(*) n FROM flota")["n"] == n + 1     # escribe normalmente
    con = A._conectar(); con.execute("DELETE FROM flota WHERE patente='AA111AA'"); con.commit(); con.close()
    # entrar a /demo estando logueado NO le pisa la sesion
    r = a.get("/demo")
    assert r.status_code == 302
    with a.session_transaction() as s:
        assert s["rol"] == "superadmin" and s["uid"] != "demo"
    assert a.get("/config").status_code == 200
    r = a.get("/salir")
    assert r.headers["Location"].endswith("/login")
    # y el superadmin no puede borrar la empresa demo
    a = login_admin(); a.get("/config")
    with a.session_transaction() as s:
        tk = s["_csrf"]
    a.post(f"/empresas/borrar/{demo}", data={"_csrf": tk})
    assert uno("SELECT COUNT(*) n FROM empresas WHERE demo=1")["n"] == 1

def test_demo_logout_vuelve_a_la_raiz(demo):
    c = cliente_demo()
    r = c.get("/salir")
    assert r.status_code == 302 and r.headers["Location"].endswith("/") and not r.headers["Location"].endswith("/login")
    with c.session_transaction() as s:
        assert "uid" not in s

def test_demo_sin_empresa_sembrada_responde_503():
    quitar_demo(); A.USOS_DEMO.clear()
    assert app.test_client().get("/demo").status_code == 503
