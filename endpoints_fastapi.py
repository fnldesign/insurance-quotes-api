from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import text
from app import conn, engine, LOCK, logger, validar, calcular, serialize_row, get_db_health, get_db_stats, get_log_info, limpar_cpf, inferir_sexo_api
import psutil

router = APIRouter()

class CotacaoInput(BaseModel):
    nome: str
    cpf: str
    sexo: str
    dtnasc: str
    capital: float
    inicio_vig: str
    fim_vig: str

class CotacaoOutput(CotacaoInput):
    id: str
    taxa_base_anual: float
    taxa_ajustada: float
    vigencia_dias: int
    vigencia_anos: float
    premio: float
    descricao: str

@router.get("/health")
def health():
    db_health = get_db_health()
    health_status = {
        "status": "healthy" if db_health["status"] == "connected" else "unhealthy",
        "database": db_health
    }
    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(content=health_status, status_code=status_code)

@router.get("/info")
def info():
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

@router.get("/cotacoes", response_model=List[CotacaoOutput])
def list_cotacoes():
    with conn() as cx:
        res = cx.execute(text("SELECT * FROM cotacoes ORDER BY id DESC"))
        rows = res.mappings().all()
    return [serialize_row(dict(r)) for r in rows]

@router.post("/cotacoes", response_model=CotacaoOutput)
def create_cotacao(payload: CotacaoInput):
    erros = validar(payload.dict())
    if erros:
        raise HTTPException(status_code=400, detail=erros)
    calc = calcular(payload.dict())
    registro = {
        "nome": payload.nome.strip(),
        "cpf": limpar_cpf(str(payload.cpf)),
        "sexo": inferir_sexo_api(payload.nome.strip()) or payload.sexo,
        "dtnasc": payload.dtnasc,
        "capital": float(payload.capital),
        "inicio_vig": payload.inicio_vig,
        "fim_vig": payload.fim_vig,
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
            res = cx.execute(text(insert_sql.text + " RETURNING id"), params)
            registro_id = int(res.scalar_one())
        elif engine.dialect.name == "mysql":
            cx.execute(insert_sql, params)
            last_id = cx.execute(text("SELECT LAST_INSERT_ID()"))
            registro_id = int(last_id.scalar()) if last_id is not None else 0
        else:
            cx.execute(insert_sql, params)
            last_id = cx.execute(text("SELECT last_insert_rowid()"))
            registro_id = int(last_id.scalar()) if last_id is not None else 0
    registro_out = {"id": str(registro_id), **registro}
    return registro_out

@router.get("/cotacoes/{id_}", response_model=CotacaoOutput)
def get_cotacao(id_: str):
    with conn() as cx:
        res = cx.execute(text("SELECT * FROM cotacoes WHERE id = :id"), {"id": id_})
        r = res.mappings().first()
    if r is None:
        raise HTTPException(status_code=404, detail="Cotação não localizada")
    return serialize_row(r)
