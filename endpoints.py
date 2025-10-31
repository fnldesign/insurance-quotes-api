"""
API endpoints blueprint for Insurance Quotes API.
Contains all REST API routes organized with Flask-RESTX namespaces.
"""
from flask import Blueprint, request
from flask_restx import Api, Resource, fields
from sqlalchemy import text
from datetime import date
import requests
import psutil

# These imports work because this module is imported AFTER app.py has defined everything
# Import happens in app.py at line 474: "from endpoints import api_bp"
from app import (
    conn, engine, LOCK, logger, validar, calcular,
    serialize_row, get_db_health, get_db_stats, get_log_info,
    limpar_cpf, inferir_sexo_api
)


# Create Blueprint
api_bp = Blueprint('api', __name__)

# Configure Flask-RESTX API on the blueprint
api = Api(
    api_bp,
    version='1.0',
    title='Insurance API',
    description='API for insurance quotes management',
    doc='/swagger'
)

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
        return [serialize_row(dict(r)) for r in rows]

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
        return serialize_row(r)
