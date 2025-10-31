import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, make_response
from flask_restx import Api, Resource, fields
from datetime import datetime, date
import sqlite3, os, threading, glob
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import MetaData, Table, Column, Integer, String, Float, Text, DateTime
import requests
import psutil

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Configuration from environment variables
DATA_DIR = os.getenv("DATA_DIR", "data")
DB_NAME = os.getenv("DB_NAME", "insurance.db")
DATABASE_URL = os.getenv("DATABASE_URL")  # optional, prefer sqlite URLs like sqlite:///path
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOGS_PATH = os.getenv("LOGS_PATH", "logs")
MAX_LOG_FILES = int(os.getenv("MAX_LOG_FILES", "30"))
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "*")
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "Content-Type, X-Debug")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET, POST, OPTIONS")
# Use "or" fallback to protect against empty-string environment values
HOST = os.getenv("HOST") or "0.0.0.0"
PORT = int(os.getenv("PORT") or "5000")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
from urllib.parse import quote_plus

# Normalize JDBC-style MySQL URLs and inject credentials if DB_USER/DB_PASS are provided.
if DATABASE_URL:
    # Support JDBC MySQL URL like: jdbc:mysql://host:3306/dbname
    if DATABASE_URL.startswith("jdbc:mysql://"):
        # strip the "jdbc:" prefix and add SQLAlchemy MySQL driver
        rest = DATABASE_URL.replace("jdbc:", "", 1)
        # use pymysql driver by default
        sqlalchemy_url = rest.replace("mysql://", "mysql+pymysql://", 1)
        # If separate DB_USER/DB_PASS env vars are provided, inject them into the URL
        if DB_USER and DB_PASS:
            try:
                # rest after driver (mysql+pymysql://host:port/db)
                after_scheme = sqlalchemy_url.split("//", 1)[1]
                sqlalchemy_url = f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASS)}@{after_scheme}"
            except Exception:
                # fallback to best-effort
                pass
        DATABASE_URL = sqlalchemy_url
    else:
        # If user supplied a mysql:// URL without a driver, add pymysql driver
        if DATABASE_URL.startswith("mysql://") and "mysql+" not in DATABASE_URL:
            DATABASE_URL = DATABASE_URL.replace("mysql://", "mysql+pymysql://", 1)
STARTUP_HEALTH_CHECK = str(os.getenv("STARTUP_HEALTH_CHECK", "false")).lower() in ("1", "true", "yes")

# Detect serverless/runtime mode and logging preferences
SERVERLESS = str(os.getenv("SERVERLESS", "false")).lower() in ("1", "true", "yes")
USE_FILE_LOGS = str(os.getenv("USE_FILE_LOGS", "true")).lower() in ("1", "true", "yes")
if SERVERLESS:
    # On serverless platforms (Vercel), avoid file writes by default
    USE_FILE_LOGS = False

# Basic console logger so we can report issues creating dirs
logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
                    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

# Determine DB path: prefer sqlite URLs if provided
EXTERNAL_DB_URL = None
if DATABASE_URL:
    if DATABASE_URL.startswith("sqlite:///"):
        # sqlite:///absolute/path
        DB_PATH = DATABASE_URL.replace("sqlite:///", "", 1)
        DATA_DIR = os.path.dirname(DB_PATH) or DATA_DIR
    elif DATABASE_URL.startswith("sqlite://"):
        # sqlite://:memory: or similar
        DB_PATH = DATABASE_URL.replace("sqlite://", "", 1)
    else:
        EXTERNAL_DB_URL = DATABASE_URL
        # fallback to local sqlite in DATA_DIR (ephemeral on serverless)
        DB_PATH = os.path.join(DATA_DIR, DB_NAME)
else:
    DB_PATH = os.path.join(DATA_DIR, DB_NAME)

# Ensure DATA_DIR exists when possible; on serverless prefer /tmp
if SERVERLESS:
    # Use ephemeral tmp directory on serverless platforms
    DATA_DIR = os.getenv("DATA_DIR", "/tmp")
    DB_PATH = os.path.join(DATA_DIR, DB_NAME) if not EXTERNAL_DB_URL else DB_PATH
else:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        root_logger.warning(f"Could not create DATA_DIR '{DATA_DIR}': {e}. Falling back to /tmp")
        DATA_DIR = "/tmp"
        DB_PATH = os.path.join(DATA_DIR, DB_NAME)

# Logging setup: only create file-based logs when enabled and writable
LOG_FILE = None
if USE_FILE_LOGS and not SERVERLESS:
    try:
        os.makedirs(LOGS_PATH, exist_ok=True)
        LOG_FILE = os.path.join(LOGS_PATH, "app.log")
    except Exception as e:
        root_logger.warning(f"Could not create LOGS_PATH '{LOGS_PATH}': {e}. Disabling file logging.")
        USE_FILE_LOGS = False

# Configure root logger (we'll add handlers below)
logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

# Remove any existing handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Console handler (always enabled)
console_handler = logging.StreamHandler()
console_handler.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
console_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# File handler only when enabled and LOG_FILE is configured
if USE_FILE_LOGS and LOG_FILE:
    try:
        file_handler = TimedRotatingFileHandler(
            filename=LOG_FILE,
            when='midnight',
            interval=1,
            backupCount=MAX_LOG_FILES,
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
        file_formatter = logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.suffix = "%Y-%m-%d"
        logger.addHandler(file_handler)
    except Exception as e:
        root_logger.warning(f"Failed to initialize file logging ({e}). Continuing with console logging only.")

# Create a named logger for the application
logger = logging.getLogger("insurance_app")

app = Flask(__name__)
LOCK = threading.Lock()

# Configure API and Swagger
api = Api(app, version='1.0', title='Insurance API',
          description='API for insurance quotes management',
          doc='/swagger')

# Define namespaces
ns_health = api.namespace('health', description='Health checks')
ns_info = api.namespace('info', description='System information')
ns_cotacoes = api.namespace('cotacoes', description='Insurance quotes operations')

# Define models/schemas
error_model = api.model('Error', {
    'erro': fields.String(required=True, description='Error type'),
    'detalhes': fields.Raw(description='Error details')
})

health_model = api.model('Health', {
    'status': fields.String(required=True, example='healthy'),
    'database': fields.Nested(api.model('DatabaseHealth', {
        'status': fields.String(required=True, example='connected'),
        'response_time': fields.Float(required=True, example=0.023)
    }))
})

info_model = api.model('Info', {
    'database': fields.Nested(api.model('DatabaseInfo', {
        'total_records': fields.Integer(required=True, example=1250)
    })),
    'logs': fields.Nested(api.model('LogsInfo', {
        'total_size_mb': fields.Float(required=True, example=15.4),
        'files_count': fields.Integer(required=True, example=7)
    })),
    'memory': fields.Nested(api.model('MemoryInfo', {
        'used_mb': fields.Float(required=True, example=128.5),
        'total_mb': fields.Float(required=True, example=1024)
    }))
})

cotacao_input = api.model('CotacaoInput', {
    'nome': fields.String(required=True, description='Nome completo'),
    'cpf': fields.String(required=True, description='CPF (11 dígitos)'),
    'sexo': fields.String(required=True, description='Sexo (M ou F)', enum=['M', 'F']),
    'dtnasc': fields.Date(required=True, description='Data de nascimento (YYYY-MM-DD)'),
    'capital': fields.Float(required=True, description='Valor do capital'),
    'inicio_vig': fields.Date(required=True, description='Início da vigência (YYYY-MM-DD)'),
    'fim_vig': fields.Date(required=True, description='Fim da vigência (YYYY-MM-DD)')
})

cotacao_output = api.inherit('CotacaoOutput', cotacao_input, {
    'id': fields.String(required=True, description='ID da cotação'),
    'taxa_base_anual': fields.Float(required=True, description='Taxa base anual'),
    'taxa_ajustada': fields.Float(required=True, description='Taxa ajustada'),
    'vigencia_dias': fields.Integer(required=True, description='Dias de vigência'),
    'vigencia_anos': fields.Float(required=True, description='Anos de vigência'),
    'premio': fields.Float(required=True, description='Valor do prêmio'),
    'descricao': fields.String(required=True, description='Descrição da cotação')
})

# ---------- Utilidades ----------
def iso(d: date) -> str: return d.strftime("%Y-%m-%d")
def parse_iso(s: str) -> date: return datetime.strptime(s, "%Y-%m-%d").date()
def limpar_cpf(c: str) -> str: return "".join(ch for ch in c if ch.isdigit())
def idade_em(birth: date, ref: date) -> int:
    anos = ref.year - birth.year
    if (ref.month, ref.day) < (birth.month, birth.day): anos -= 1
    return anos

def serialize_row(row_dict):
    """Convert datetime objects to ISO format strings for JSON serialization."""
    result = {}
    for key, value in row_dict.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, date):
            result[key] = value.strftime("%Y-%m-%d")
        else:
            result[key] = value
    # Ensure id is a string for API consistency
    if 'id' in result and result['id'] is not None:
        result['id'] = str(result['id'])
    return result

# Titles to help infer gender from name
MALE_TITLES = [
    "Sr.", "Senhor", "Mr.", "Dr.", "Doutor", "Prof.", "Professor", "Mestre", "Rev.", "Reverendo",
    "Pe.", "Padre", "Cônego", "Mons.", "Monsenhor", "Bispo", "Arcebispo", "Cardeal", "Papa",
    "Eng.", "Engenheiro", "Arq.", "Arquiteto", "Adv.", "Advogado", "Des.", "Desembargador",
    "Min.", "Ministro", "Pres.", "Presidente", "Gov.", "Governador", "Dep.", "Deputado",
    "Sen.", "Senador", "Ver.", "Vereador", "Cel.", "Coronel", "Cap.", "Capitão", "Maj.", "Major",
    "Gen.", "General", "Alm.", "Almirante", "Cmd.", "Comandante", "Dir.", "Diretor",
    "Coord.", "Coordenador", "Superint.", "Superintendente", "CEO", "CFO", "COO", "CTO",
    "Dom", "Príncipe", "Rei", "Barão", "Conde", "Duque", "Marquês", "Sir", "Lord"
]

FEMALE_TITLES = [
    "Sra.", "Senhora", "Mrs.", "Miss", "Ms.", "Dra.", "Doutora", "Profa.", "Professora",
    "Mestra", "Revda.", "Reverenda", "Madre", "Irmã", "Cônega", "Bispa", "Arcebispa",
    "Enga.", "Engenheira", "Arqa.", "Arquiteta", "Adva.", "Advogada", "Desa.", "Desembargadora",
    "Mina.", "Ministra", "Presa.", "Presidente", "Gova.", "Governadora", "Depa.", "Deputada",
    "Sena.", "Senadora", "Vera.", "Vereadora", "Cela.", "Coronela", "Capa.", "Capitã",
    "Maja.", "Major", "Gena.", "General", "Alma.", "Almirante", "Cmda.", "Comandante",
    "Dira.", "Diretora", "Coorda.", "Coordenadora", "Superinta.", "Superintendente",
    "CEO", "CFO", "COO", "CTO", "Dona", "Princesa", "Rainha", "Baronesa", "Condessa",
    "Duquesa", "Marquesa", "Lady", "Madame"
]

def inferir_sexo_api(nome: str) -> str:
    if any(title in nome for title in MALE_TITLES):
        return "M"
    if any(title in nome for title in FEMALE_TITLES):
        return "F"
    
    resp = requests.get("https://api.genderize.io", params={"name": nome})
    data = resp.json()
    # retorna 'M', 'F' ou None
    if data.get("gender") == "male":
        return "M"
    elif data.get("gender") == "female":
        return "F"
    return "M"

# Create SQLAlchemy engine (will use DATABASE_URL if provided, otherwise sqlite file)
try:
    if EXTERNAL_DB_URL:
        engine = create_engine(EXTERNAL_DB_URL, pool_pre_ping=True)
    else:
        # sqlite file path
        engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
except Exception as e:
    root_logger.error(f"Failed to create DB engine: {e}")
    raise

def conn():
    """Return a SQLAlchemy Connection (context manager). Use like: with conn() as c: c.execute(text(...))"""
    return engine.connect()

def init_db():
    """Create the `cotacoes` table using SQLAlchemy Table metadata so the DDL is dialect-appropriate."""
    try:
        metadata = MetaData()
        cotacoes = Table(
            'cotacoes', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('nome', String(255), nullable=False),
            Column('cpf', String(32), nullable=False),
            Column('sexo', String(3), nullable=False),
            Column('dtnasc', String(20), nullable=False),
            Column('capital', Float, nullable=False),
            Column('inicio_vig', String(20), nullable=False),
            Column('fim_vig', String(20), nullable=False),
            Column('taxa_base_anual', Float, nullable=False),
            Column('taxa_ajustada', Float, nullable=False),
            Column('vigencia_dias', Integer, nullable=False),
            Column('vigencia_anos', Float, nullable=False),
            Column('premio', Float, nullable=False),
            Column('descricao', Text),
            Column('created_at', DateTime, server_default=text('CURRENT_TIMESTAMP'))
        )
        metadata.create_all(engine)
    except SQLAlchemyError as e:
        logger.error(f"Failed to initialize database: {e}")

init_db()

def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    resp.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    return resp

def get_db_health():
    try:
        start_time = datetime.now()
        with conn() as cx:
            cx.execute(text("SELECT 1")).fetchone()
        response_time = (datetime.now() - start_time).total_seconds()
        return {"status": "connected", "response_time": round(response_time, 3)}
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {"status": "disconnected", "error": str(e)}

def get_log_info():
    try:
        log_files = glob.glob(os.path.join(LOGS_PATH, "*.log*"))
        total_size = sum(os.path.getsize(f) for f in log_files)
        return {
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "files_count": len(log_files)
        }
    except Exception as e:
        logger.error(f"Error getting log info: {e}")
        return {"error": str(e)}

def get_db_stats():
    try:
        with conn() as cx:
            res = cx.execute(text("SELECT COUNT(*) FROM cotacoes"))
            count = res.scalar() if res is not None else 0
        return {"total_records": count}
    except Exception as e:
        logger.error(f"Error getting database stats: {e}")
        return {"error": str(e)}


# ---------- Startup logging ----------
def _format_db_display():
    # Provide a sanitized, human-friendly DB description for logs.
    try:
        backend = engine.dialect.name
    except Exception:
        backend = 'unknown'

    # Try to build a redacted display string: omit credentials but show host/port/db
    try:
        url = engine.url
        scheme = getattr(url, 'drivername', str(url).split(':', 1)[0])
        if scheme.startswith('sqlite'):
            # For sqlite show the file path
            try:
                db_path = url.database
                db_display = f"sqlite:///{db_path}"
            except Exception:
                db_display = f"sqlite:///{DB_PATH}"
        else:
            host = getattr(url, 'host', None) or ''
            port = getattr(url, 'port', None)
            database = getattr(url, 'database', '') or ''
            # show scheme://host:port/database (no username/password)
            hostport = f":{port}" if port else ""
            db_display = f"{scheme}://{host}{hostport}/{database}"
    except Exception:
        # Fallback to the raw DATABASE_URL but avoid printing credentials if possible
        try:
            db_display = DATABASE_URL or f"sqlite:///{DB_PATH}"
        except Exception:
            db_display = f"sqlite:///{DB_PATH}"

    return backend, db_display


def log_startup():
    backend, db_display = _format_db_display()
    logger.info("Starting insurance_app")
    logger.info("Configuration: SERVERLESS=%s, USE_FILE_LOGS=%s, LOGS_PATH=%s", SERVERLESS, USE_FILE_LOGS, LOGS_PATH)
    logger.info("DB backend: %s", backend)
    logger.info("DB url: %s", db_display)
    if SERVERLESS:
        logger.info("Running in SERVERLESS mode: file logging disabled and filesystem writes avoided. HOST/PORT env vars will be ignored.")
    else:
        logger.info("Server will bind to %s:%s", HOST, PORT)


def _startup_health_check():
        """Background health check that calls the /health endpoint once after startup."""

        # Wait briefly for server to start
        import time
        time.sleep(5)
        url = f"http://{HOST}:{PORT}/health/"

        log_startup()

        # Direct DB connection health check
        logger.info("Running startup DB health check (direct connection)")        

        try:
            t0 = time.perf_counter()
            with conn() as cx:
                cx.execute(text("SELECT 1")).fetchone()
            dt = time.perf_counter() - t0
            logger.info("Startup DB health: connected (%.3fs)", dt)
        except Exception as e:
            logger.warning("Startup DB health: disconnected (%s)", e)

        # HTTP health endpoint check
        logger.info("Running startup HTTP health check against %s", url)
        try:
            resp = requests.get(url, timeout=5)
            logger.info("Startup HTTP health returned %s: %s", resp.status_code, resp.text[:500])
        except Exception as e:
            logger.warning("Startup HTTP health failed: %s", e)       
            logger.debug("_startup_health_check encountered an error: %s", e)

# ---------- Validação + Cálculo ----------
def validar(p):
    erros = []
    req = ["nome","cpf","sexo","dtnasc","capital","inicio_vig","fim_vig"]
    falta = [k for k in req if k not in p]
    if falta: erros.append({"campo":"geral","mensagem":f"Faltam: {', '.join(falta)}"})

    if erros: return erros

    if not isinstance(p["nome"], str) or not p["nome"].strip():
        erros.append({"campo":"nome","mensagem":"Nome inválido"})

    cpf = limpar_cpf(str(p["cpf"]))
    if len(cpf)!=11 or not cpf.isdigit():
        erros.append({"campo":"cpf","mensagem":"CPF deve ter 11 dígitos numéricos"})

    if str(p["sexo"]) not in ("M","F"):
        erros.append({"campo":"sexo","mensagem":"Sexo deve ser 'M' ou 'F'"})

    try: dtnasc = parse_iso(p["dtnasc"])
    except: erros.append({"campo":"dtnasc","mensagem":"Use yyyy-MM-dd"}); dtnasc=None

    try: inicio = parse_iso(p["inicio_vig"])
    except: erros.append({"campo":"inicio_vig","mensagem":"Use yyyy-MM-dd"}); inicio=None

    try: fim = parse_iso(p["fim_vig"])
    except: erros.append({"campo":"fim_vig","mensagem":"Use yyyy-MM-dd"}); fim=None

    if inicio and fim and fim <= inicio:
        erros.append({"campo":"fim_vig","mensagem":"Fim deve ser posterior ao início"})

    try:
        capital = float(p["capital"])
        if capital<=0: erros.append({"campo":"capital","mensagem":"Capital deve ser > 0"})
    except:
        erros.append({"campo":"capital","mensagem":"Capital numérico"})

    if dtnasc and inicio:
        idade = idade_em(dtnasc, inicio)
        if idade < 18 or idade > 80:
            erros.append({"campo":"dtnasc","mensagem":"Idade no início deve estar entre 18 e 80 anos"})

    return erros

def calcular(p):
    sexo = p["sexo"]
    dtnasc = parse_iso(p["dtnasc"])
    capital = float(p["capital"])
    inicio = parse_iso(p["inicio_vig"])
    fim = parse_iso(p["fim_vig"])

    taxa_base_anual = 0.01
    taxa_ajustada = taxa_base_anual

    if sexo == "F":
        taxa_ajustada *= 0.95
    if idade_em(dtnasc, inicio) > 60:
        taxa_ajustada *= 1.2

    dias = (fim - inicio).days
    anos_vig = round(dias/365, 2)
    premio = round(capital * taxa_ajustada * anos_vig, 2)

    return {
        "taxa_base_anual": taxa_base_anual,
        "taxa_ajustada": round(taxa_ajustada, 4),
        "vigencia_dias": dias,
        "vigencia_anos": anos_vig,
        "premio": premio
    }

# ---------- API Endpoints ----------
@ns_health.route('/')
class HealthCheck(Resource):
    @api.response(200, 'API is healthy', health_model)
    @api.response(503, 'Service unavailable')
    def get(self):
        """Check the health status of the API and its dependencies"""
        db_health = get_db_health()
        health_status = {
            "status": "healthy" if db_health["status"] == "connected" else "unhealthy",
            "database": db_health
        }
        status_code = 200 if health_status["status"] == "healthy" else 503
        return health_status, status_code

@ns_info.route('/')
class SystemInfo(Resource):
    @api.response(200, 'System information retrieved', info_model)
    def get(self):
        """Get system information including database stats, log sizes, and memory usage"""
        process = psutil.Process()
        memory_info = process.memory_info()
        
        info = {
            "database": get_db_stats(),
            "logs": get_log_info(),
            "memory": {
                "used_mb": round(memory_info.rss / (1024 * 1024), 2),
                "total_mb": round(psutil.virtual_memory().total / (1024 * 1024), 2)
            }
        }
        return info

@ns_cotacoes.route('/')
class CotacoesList(Resource):
    @api.doc('list_cotacoes')
    @api.response(200, 'Success', [cotacao_output])
    def get(self):
        """List all insurance quotes"""
        with conn() as cx:
            res = cx.execute(text("SELECT * FROM cotacoes ORDER BY id DESC"))
            rows = res.mappings().all()
        return [serialize_row(r) for r in rows]

    @api.doc('create_cotacao')
    @api.expect(cotacao_input)
    @api.response(201, 'Quote created successfully', cotacao_output)
    @api.response(400, 'Validation error', error_model)
    def post(self):
        """Create a new insurance quote"""
        try:
            payload = request.get_json(force=True)
        except Exception as e:
            api.abort(400, erro="Erro de validação", 
                     detalhes=[{"campo":"body","mensagem":"JSON inválido"}])

        erros = validar(payload)
        if erros:
            api.abort(400, erro="Erro de validação", detalhes=erros)

        calc = calcular(payload)
        registro = {
            "nome": payload["nome"].strip(),
            "cpf": limpar_cpf(str(payload["cpf"])),
            "sexo": inferir_sexo_api(payload["nome"].strip()) or payload["sexo"],
            "dtnasc": payload["dtnasc"],
            "capital": float(payload["capital"]),
            "inicio_vig": payload["inicio_vig"],
            "fim_vig": payload["fim_vig"],
            **calc,
            "descricao": f"Seguro prestamista com taxa base anual de {round(calc['taxa_base_anual']*100, 2)}% e ajustes por sexo/idade de {round(calc['taxa_ajustada']*100, 2)}%."
        }

        insert_sql = text("""
            INSERT INTO cotacoes
            (nome,cpf,sexo,dtnasc,capital,inicio_vig,fim_vig,
             taxa_base_anual,taxa_ajustada,vigencia_dias,vigencia_anos,premio,descricao)
            VALUES (:nome,:cpf,:sexo,:dtnasc,:capital,:inicio_vig,:fim_vig,
                    :taxa_base_anual,:taxa_ajustada,:vigencia_dias,:vigencia_anos,:premio,:descricao)
        """)

        params = {
            "nome": registro["nome"],
            "cpf": registro["cpf"],
            "sexo": registro["sexo"],
            "dtnasc": registro["dtnasc"],
            "capital": registro["capital"],
            "inicio_vig": registro["inicio_vig"],
            "fim_vig": registro["fim_vig"],
            "taxa_base_anual": registro["taxa_base_anual"],
            "taxa_ajustada": registro["taxa_ajustada"],
            "vigencia_dias": registro["vigencia_dias"],
            "vigencia_anos": registro["vigencia_anos"],
            "premio": registro["premio"],
            "descricao": registro["descricao"],
        }

        with LOCK, engine.begin() as cx:
            if engine.dialect.name in ("postgresql", "postgres"):
                # Postgres supports RETURNING
                res = cx.execute(text(insert_sql.text + " RETURNING id"), params)
                registro_id = int(res.scalar_one())
            elif engine.dialect.name == "mysql":
                # MySQL: use LAST_INSERT_ID()
                cx.execute(insert_sql, params)
                last_id = cx.execute(text("SELECT LAST_INSERT_ID()")).scalar()
                registro_id = int(last_id) if last_id is not None else 0
            else:
                # SQLite: use last_insert_rowid()
                cx.execute(insert_sql, params)
                last_id = cx.execute(text("SELECT last_insert_rowid()")).scalar()
                registro_id = int(last_id) if last_id is not None else 0

        registro_out = {"id": str(registro_id), **registro}
        return registro_out, 201

@ns_cotacoes.route('/<id_>')
@api.doc(params={'id_': 'The quote ID'})
class Cotacao(Resource):
    @api.doc('get_cotacao')
    @api.response(200, 'Success', cotacao_output)
    @api.response(404, 'Quote not found', error_model)
    def get(self, id_):
        """Get an insurance quote by ID"""
        with conn() as cx:
            res = cx.execute(text("SELECT * FROM cotacoes WHERE id = :id"), {"id": id_})
            r = res.mappings().first()
        if r is None:
            api.abort(404, erro="Não encontrado", mensagem="Cotação não localizada")
            return
        return serialize_row(r)

if __name__ == "__main__":
    # Emit startup configuration to logs so runtime behavior is visible (helpful in Vercel logs)
    try:
        log_startup()
    except Exception as e:
        # If logging the startup fails, print to console as a last resort
        root_logger.warning(f"Startup logging failed: {e}")

    # When running in serverless mode, do NOT start the Flask development server.
    # This ensures HOST/PORT env vars are ignored in serverless deployments.
    if SERVERLESS:
        logger.info("SERVERLESS mode enabled — skipping app.run(). The platform will invoke the function handler.")
    else:
        # Optionally run a startup health check in a background thread
        if STARTUP_HEALTH_CHECK:
            try:
                t = threading.Thread(target=_startup_health_check, daemon=True)
                t.start()
            except Exception as e:
                logger.warning("Failed to start startup health check thread: %s", e)

        app.run(host=HOST, port=PORT, debug=(os.getenv("FLASK_DEBUG", "0") == "1"))