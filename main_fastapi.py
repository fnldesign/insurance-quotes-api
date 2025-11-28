from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from endpoints_fastapi import router as api_router

app = FastAPI(title="Insurance API", description="API for insurance quotes management")

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("CORS_ALLOW_ORIGIN", "*")],
    allow_credentials=True,
    allow_methods=[os.getenv("CORS_ALLOW_METHODS", "GET, POST, OPTIONS")],
    allow_headers=[os.getenv("CORS_ALLOW_HEADERS", "Content-Type, X-Debug")],
)

@app.get("/", response_class=HTMLResponse)
def read_root():
    try:
        file_path = os.path.join(os.path.dirname(__file__), "public", "landing_page.html")
        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        html = """
        <!doctype html><html><head><meta charset='utf-8'/><title>Insurance API</title></head><body><h1>Insurance API</h1><p>Landing page not found. Visit <a href='/docs'>Swagger UI</a>.</p></body></html>
        """
    return HTMLResponse(content=html, status_code=200)

# Include API router
app.include_router(api_router)
