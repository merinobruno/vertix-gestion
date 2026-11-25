from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from panel_api import ApiError, service_info, submit, upload_for
from shared_agenda import router as shared_agenda


app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
app.include_router(shared_agenda)
PRIVATE_HEADERS = {"Cache-Control": "private, no-store"}


@app.exception_handler(ApiError)
async def api_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=PRIVATE_HEADERS)


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    return JSONResponse(service_info(pdf_export=False, shared_directory=True), headers=PRIVATE_HEADERS)


async def download_chunks(data):
    for offset in range(0, len(data), 64 * 1024):
        yield data[offset:offset + 64 * 1024]


@app.post("/api/anexo")
@app.post("/api/generate")
async def receive(request: Request):
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    local = request.url.hostname in ("127.0.0.1", "localhost", "::1")
    if origin and origin != f"https://{host}" and not (local and origin == f"http://{host}"):
        raise ApiError(403, "La solicitud debe iniciarse desde el panel.")

    path = request.url.path
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    spec = upload_for(path, content_type)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > spec.limit:
            raise ApiError(413, spec.size_error)
        body.extend(chunk)
    if not body:
        raise ApiError(413, spec.size_error)

    name, mime, data = await run_in_threadpool(submit, path, body, pdf_export=False)
    if name is None:
        return Response(data, media_type=mime, headers=PRIVATE_HEADERS)
    headers = {**PRIVATE_HEADERS, "Content-Disposition": f'attachment; filename="{name}"'}
    return StreamingResponse(download_chunks(data), media_type=mime, headers=headers)


app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="panel")
