# Prepara el entorno de los tests ANTES de importar app.py. Lo importan los archivos de test.
#   - Sin nada: la suite corre contra SQLite (archivo temporal), como siempre.
#   - Con TEST_DATABASE_URL=postgresql://...: corre contra ese PostgreSQL (se vacia el esquema "public").
#     Lo arma solo tests/run_pg_tests.sh con una base efimera.
import os, sys, tempfile
from urllib.parse import urlparse

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

PG_URL = os.environ.get("TEST_DATABASE_URL")
os.environ.pop("DATABASE_URL", None)
os.environ.update(ADMIN_PASSWORD="clave-de-test-123", SECRET_KEY="secret-de-test")
TMP = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(TMP, "test.db")

if PG_URL:
    # Seguro anti-desastre: esta preparacion BORRA todas las tablas, asi que solo se acepta
    # una base local o con "test" en el nombre. Nunca la de produccion.
    u = urlparse(PG_URL)
    if u.hostname not in ("localhost", "127.0.0.1", "::1") and "test" not in (u.path or "").lower():
        sys.exit("TEST_DATABASE_URL: por seguridad solo se aceptan bases locales o con 'test' en el nombre")
    import psycopg2
    _c = psycopg2.connect(PG_URL); _c.autocommit = True
    _c.cursor().execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public")
    _c.close()
    os.environ["DATABASE_URL"] = PG_URL
