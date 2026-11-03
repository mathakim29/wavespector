# /// script
# requires-python = "==3.11.*"
# dependencies = [
#     "audio-separator[gpu]>=0.47.1",
#     "audioread>=3.1.0",
#     "fastapi[standard]>=0.162.0",
#     "hashids>=1.3.1",
#     "python-dotenv>=1.0.1",
#     "spoolctl>=0.4.11",
#     "yt-dlp>=2026.10.21",
# ]
# ///

import logging
import json
import os
import sys
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import yt_dlp
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from fastapi.openapi.utils import get_openapi
from audio_separator.separator import Separator

from hashids import Hashids

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("audio_separator_api")


from contextlib import asynccontextmanager
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        refresh_model_catalog()
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        logger.error("Unable to load model catalog at startup: %s", exc)
    yield


app = FastAPI(title="Audio Separator API", lifespan=lifespan)
cors_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", ".asep")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Keep downloaded audio-separator models with the project. Deployments can
# override this cache location with AUDIO_SEPARATOR_MODEL_DIR.
PROJECT_DIR = Path(__file__).resolve().parent
MODEL_DIR = Path(
    os.environ.get("AUDIO_SEPARATOR_MODEL_DIR", PROJECT_DIR / ".asep" / "models")
).expanduser()
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# IDs exposed by the API are opaque Hashids values.  The salt is configurable
# so deployments can keep IDs stable across restarts without exposing the
# underlying UUIDs.
JOB_ID_SALT = os.environ.get("JOB_ID_SALT", "moses-ai")
hashids = Hashids(salt=JOB_ID_SALT, min_length=10)
JOBS: dict[str, dict] = {}
MODEL_CATALOG: dict = {}


def refresh_model_catalog() -> dict:
    """Load the available model catalog once and keep it in memory."""
    global MODEL_CATALOG
    result = subprocess.run(
        ["audio-separator", "-l", "--list_format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    MODEL_CATALOG = json.loads(result.stdout)
    logger.info(
        "Loaded %d model categories",
        len(MODEL_CATALOG),
    )
    return MODEL_CATALOG


def available_model_names() -> list[str]:
    """Return the model filenames exposed by the /models catalog."""
    return sorted(
        {
            model["filename"]
            for category in MODEL_CATALOG.values()
            for model in category.values()
            if model.get("filename")
        }
    )


def custom_openapi():
    """Document the dynamic /models catalog as the /separate enum."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version="0.1.0",
        routes=app.routes,
    )
    request_body = schema.get("paths", {}).get("/separate", {}).get("post", {}).get("requestBody", {})
    form_schema = request_body.get("content", {}).get("multipart/form-data", {}).get("schema", {})
    schema_ref = form_schema.get("$ref", "").rsplit("/", 1)[-1]
    if schema_ref:
        properties = schema.get("components", {}).get("schemas", {}).get(schema_ref, {}).get("properties", {})
        if "model_name" in properties:
            properties["model_name"]["enum"] = available_model_names()

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi



def job_log(job_id: str, message: str) -> None:
    JOBS[job_id].setdefault("logs", []).append(message)


def reset_model_cache() -> None:
    """Remove cached model artifacts so the next load downloads them again."""
    shutil.rmtree(MODEL_DIR, ignore_errors=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)


class ModelSelection(BaseModel):
    model_name: str = "UVR-MDX-NET-Inst_HQ_3.onnx"

    @field_validator("model_name")
    @classmethod
    def model_must_be_available(cls, value: str) -> str:
        try:
            models = MODEL_CATALOG
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to identify available models: {exc}") from exc

        available = set(available_model_names())
        if value not in available:
            raise ValueError(f"Unknown model: {value}")
        return value


def new_job_id() -> str:
    return hashids.encode(uuid.uuid4().int)


def download_url(url: str, job_dir: str) -> str:
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(job_dir, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def convert_to_wav(input_path: str, job_dir: str) -> str:
    """Convert any supported input container/codec to 16-bit PCM WAV."""
    # Avoid asking ffmpeg to read and write the same WAV path.
    if input_path.lower().endswith(".wav"):
        return input_path

    wav_path = os.path.join(job_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            wav_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return wav_path


def run_separation(job_id: str, input_path: str, job_dir: str, model_name: str):
    JOBS[job_id]["status"] = "processing"
    JOBS[job_id]["started_at"] = datetime.utcnow().isoformat()
    job_log(job_id, f"Starting separation with {model_name}")
    logger.info(f"[{job_id}] Started separation | file={input_path} model={model_name}")
    try:
        input_path = convert_to_wav(input_path, job_dir)
        job_log(job_id, f"Converted input to {os.path.basename(input_path)}")
        JOBS[job_id]["processing_filename"] = os.path.basename(input_path)
        sep = Separator(
            output_dir=job_dir,
            model_file_dir=str(MODEL_DIR),
            log_level=logging.INFO,
        )
        try:
            sep.load_model(model_filename=model_name)
        except Exception as first_error:
            # A partially downloaded or corrupted model can fail during load.
            # Clear the isolated cache and retry once; any second failure is
            # allowed to reach the normal job error handler.
            logger.warning(
                "Model %s could not be loaded; clearing %s and retrying: %s",
                model_name,
                MODEL_DIR,
                first_error,
            )
            reset_model_cache()
            sep = Separator(
                output_dir=job_dir,
                model_file_dir=str(MODEL_DIR),
                log_level=logging.INFO,
            )
            sep.load_model(model_filename=model_name)
        outputs = sep.separate(input_path)

        JOBS[job_id].update(
            status="done",
            progress=100,
            outputs=outputs,
            finished_at=datetime.utcnow().isoformat(),
        )
        job_log(job_id, f"Finished separation: {len(outputs)} output files")
        logger.info(f"[{job_id}] Done | outputs={outputs}")
    except Exception as e:
        JOBS[job_id].update(
            status="failed",
            error=str(e),
            finished_at=datetime.utcnow().isoformat(),
        )
        job_log(job_id, f"ERROR: {e}")
        logger.error(f"[{job_id}] Failed | error={e}")


def run_url_separation(job_id: str, url: str, job_dir: str, model_name: str):
    JOBS[job_id]["status"] = "downloading"
    job_log(job_id, "Downloading source audio")
    try:
        input_path = download_url(url, job_dir)
        JOBS[job_id]["filename"] = os.path.basename(input_path)
        job_log(job_id, f"Downloaded {os.path.basename(input_path)}")
    except Exception as e:
        JOBS[job_id].update(
            status="failed",
            error=f"download error: {e}",
            finished_at=datetime.utcnow().isoformat(),
        )
        job_log(job_id, f"ERROR: download error: {e}")
        return
    run_separation(job_id, input_path, job_dir, model_name)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/models")
def list_models():
    return MODEL_CATALOG


@app.post("/separate")
async def separate(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    model_name: str = Form("UVR-MDX-NET-Inst_HQ_3.onnx"),
):
    if not file and not url:
        raise HTTPException(400, "Provide either a file or a url")
    if file and url:
        raise HTTPException(400, "Provide only one of file or url")

    try:
        model_name = ModelSelection(model_name=model_name).model_name
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    job_id = new_job_id()
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    if file:
        if not file.filename:
            raise HTTPException(400, "No file provided")
        input_path = os.path.join(job_dir, file.filename)
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "logs": ["Job queued"],
            "filename": file.filename,
            "created_at": datetime.utcnow().isoformat(),
        }
        background_tasks.add_task(run_separation, job_id, input_path, job_dir, model_name)
    else:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "logs": ["Job queued"],
            "filename": url,
            "created_at": datetime.utcnow().isoformat(),
        }
        background_tasks.add_task(run_url_separation, job_id, url, job_dir, model_name)

    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, **job}


@app.get("/jobs")
def list_jobs():
    return JOBS


@app.get("/download/{job_id}/{filename}")
def download(job_id: str, filename: str):
    path = os.path.join(OUTPUT_DIR, job_id, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=filename)


if __name__ == "__main__":
    """Start the Stemsplitter API server."""
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
