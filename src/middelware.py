import logging
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from src.main import app

logger = logging.getLogger(__name__)


@app.middleware("http")
async def middleware(request: Request, call_next):
    req_id = uuid.uuid4().hex
    request.state.req_id = req_id

    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error, req_id=%s", req_id)
        response = JSONResponse({"detail": "Ошибка сервера"}, status_code=500)

    response.headers["req_id"] = req_id
    return response
