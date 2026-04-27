from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.graph_builder import graph_payload
from app.suggestions import selected_title_suggested_asks, suggested_asks

router = APIRouter()


@router.get("/graph", response_class=HTMLResponse)
def graph_page(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse("graph.html", {"request": request})


@router.get("/api/graph")
def api_graph() -> dict:
    return graph_payload()


@router.get("/api/suggested-asks")
def api_suggested_asks(selected_title_id: Optional[int] = Query(default=None)) -> dict:
    if isinstance(selected_title_id, int) and selected_title_id:
        return selected_title_suggested_asks(selected_title_id)
    return suggested_asks()
