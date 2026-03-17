from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .config import load_settings
from .contracts import AxeViolationPayload, ScanAndProcessRequest
from .reporting import render_html_report, render_pdf_report
from .service import SonarA11yService

settings = load_settings()
service = SonarA11yService(settings)
app = FastAPI(title="SonarA11y Phase2 Gradient Core", version="1.0.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "phase2-gradient-core"}


@app.get("/cache/stats")
async def cache_stats():
    return JSONResponse(service.cache_stats())


@app.post("/process")
async def process_payload(payload: AxeViolationPayload):
    try:
        report = await service.process_web_payload(payload)
        return JSONResponse(report.model_dump(mode="json"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/scan-and-process")
async def scan_and_process(request: ScanAndProcessRequest):
    try:
        report = await service.scan_then_process(str(request.url), request.viewport)
        return JSONResponse(report.model_dump(mode="json"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/web/jobs")
async def create_web_job(request: ScanAndProcessRequest):
    try:
        job_id = await service.create_web_job(str(request.url), request.viewport)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"jobId": job_id, "status": "queued", "provider": "digitalocean-gradient"}


@app.get("/web/jobs/{job_id}")
async def get_web_job_status(job_id: str):
    job = service.web_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    total_nodes = max(0, job.total_nodes)
    completed_nodes = max(0, min(job.completed_nodes, total_nodes if total_nodes else job.completed_nodes))
    percent = int((completed_nodes / total_nodes) * 100) if total_nodes else 0
    elapsed_seconds = int(max(0, job.updated_at - job.created_at))

    body = {
        "jobId": job.id,
        "status": job.status,
        "stage": job.stage,
        "updatedAt": job.updated_at,
        "error": job.error,
        "provider": "digitalocean-gradient",
        "url": job.url,
        "progress": {
            "totalNodes": total_nodes,
            "completedNodes": completed_nodes,
            "percent": percent,
            "elapsedSeconds": elapsed_seconds,
        },
    }
    if job.report:
        body["traceIds"] = [r.traceId for r in job.report.results]
    return body


@app.get("/web/jobs/{job_id}/report")
async def get_web_job_report(job_id: str, format: str = Query(default="json", pattern="^(json|html|pdf)$")):
    job = service.web_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed" or not job.report:
        raise HTTPException(status_code=409, detail=f"Job not ready. status={job.status}")

    if format == "json":
        return JSONResponse(job.report.model_dump(mode="json"))

    if format == "html":
        return HTMLResponse(render_html_report(job.report))

    pdf = render_pdf_report(job.report)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=sonara11y-web-{job_id}.pdf"},
    )


@app.post("/pdf/jobs")
async def create_pdf_job(
    pdf_url: str | None = Query(default=None),
    file: UploadFile | None = File(default=None),
):
    if not pdf_url and not file:
        raise HTTPException(status_code=400, detail="Provide either `pdf_url` or an uploaded `file`.")

    try:
        if pdf_url:
            job_id = await service.create_pdf_job_from_url(pdf_url)
        else:
            data = await file.read()
            if not data:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            job_id = await service.create_pdf_job_from_bytes(data, source="upload", filename=file.filename)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"jobId": job_id, "status": "queued", "provider": "digitalocean-gradient"}


@app.get("/pdf/jobs/{job_id}")
async def get_pdf_job_status(job_id: str):
    job = service.pdf_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    body = {
        "jobId": job.id,
        "status": job.status,
        "updatedAt": job.updated_at,
        "error": job.error,
        "provider": "digitalocean-gradient",
    }
    if job.report:
        body["traceIds"] = [r.traceId for r in job.report.results]
    return body


@app.get("/pdf/jobs/{job_id}/report")
async def get_pdf_job_report(job_id: str, format: str = Query(default="json", pattern="^(json|html|pdf)$")):
    job = service.pdf_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed" or not job.report:
        raise HTTPException(status_code=409, detail=f"Job not ready. status={job.status}")

    if format == "json":
        return JSONResponse(job.report.model_dump(mode="json"))

    if format == "html":
        return HTMLResponse(render_html_report(job.report))

    pdf = render_pdf_report(job.report)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=sonara11y-{job_id}.pdf"},
    )
