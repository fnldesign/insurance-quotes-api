import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, make_response
from flask_restx import Api, Resource, fields
from datetime import datetime, date
import sqlite3, os, threading, glob
import requests
import psutil

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Configuration from environment variables
DATA_DIR = os.getenv("DATA_DIR", "data")
DB_PATH = os.path.join(DATA_DIR, os.getenv("DB_NAME", "insurance.db"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOGS_PATH = os.getenv("LOGS_PATH", "logs")
MAX_LOG_FILES = int(os.getenv("MAX_LOG_FILES", "30"))
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "*")
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "Content-Type, X-Debug")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET, POST, OPTIONS")
# Use "or" fallback to protect against empty-string environment values
HOST = os.getenv("HOST") or "0.0.0.0"
PORT = int(os.getenv("PORT") or "5000")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Logging setup
os.makedirs(LOGS_PATH, exist_ok=True)
LOG_FILE = os.path.join(LOGS_PATH, "app.log")

# Configure root logger
logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL.upper()))

# Remove any existing handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(getattr(logging, LOG_LEVEL.upper()))
console_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# Daily rotating file handler
file_handler = TimedRotatingFileHandler(
    filename=LOG_FILE,
    when='midnight',
    interval=1,
    backupCount=MAX_LOG_FILES,
    encoding='utf-8'
)
file_handler.setLevel(getattr(logging, LOG_LEVEL.upper()))
file_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(file_formatter)
file_handler.suffix = "%Y-%m-%d"  # Set the suffix for rotated files
logger.addHandler(file_handler)

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

def conn():
    cx = sqlite3.connect(DB_PATH)
    cx.row_factory = sqlite3.Row
    return cx

def init_db():
    with conn() as cx:
        cx.execute("""
        CREATE TABLE IF NOT EXISTS cotacoes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          nome TEXT NOT NULL,
          cpf TEXT NOT NULL,
          sexo TEXT NOT NULL,
          dtnasc TEXT NOT NULL,
          capital REAL NOT NULL,
          inicio_vig TEXT NOT NULL,
          fim_vig TEXT NOT NULL,
          taxa_base_anual REAL NOT NULL,
          taxa_ajustada REAL NOT NULL,
          vigencia_dias INTEGER NOT NULL,
          vigencia_anos REAL NOT NULL,
          premio REAL NOT NULL,
          descricao TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

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
            cx.execute("SELECT 1").fetchone()
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
            count = cx.execute("SELECT COUNT(*) FROM cotacoes").fetchone()[0]
        return {"total_records": count}
    except Exception as e:
        logger.error(f"Error getting database stats: {e}")
        return {"error": str(e)}

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
            rows = cx.execute("SELECT * FROM cotacoes ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

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

        with LOCK, conn() as cx:
            cur = cx.execute("""
                INSERT INTO cotacoes
                (nome,cpf,sexo,dtnasc,capital,inicio_vig,fim_vig,
                 taxa_base_anual,taxa_ajustada,vigencia_dias,vigencia_anos,premio,descricao)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (registro["nome"], registro["cpf"], registro["sexo"], registro["dtnasc"],
                  registro["capital"], registro["inicio_vig"], registro["fim_vig"],
                  registro["taxa_base_anual"], registro["taxa_ajustada"], registro["vigencia_dias"],
                  registro["vigencia_anos"], registro["premio"], registro["descricao"]))
            registro_id = cur.lastrowid
            cx.commit()

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
            r = cx.execute("SELECT * FROM cotacoes WHERE id = ?", (id_,)).fetchone()
        if not r:
            api.abort(404, erro="Não encontrado", mensagem="Cotação não localizada")
        return dict(r)

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=(os.getenv("FLASK_DEBUG", "0") == "1"))