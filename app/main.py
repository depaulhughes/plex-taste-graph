from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import init_db
from app.routes import ask, graph, home, library


def create_app() -> FastAPI:
    settings = get_settings()
    init_db()
    app = FastAPI(title=settings.app_name)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.state.templates = Jinja2Templates(directory="app/templates")
    app.include_router(home.router)
    app.include_router(library.router)
    app.include_router(graph.router)
    app.include_router(ask.router)
    return app


app = create_app()
