import cv2
import os
import io
import random
import time
import numpy as np 

from PIL import Image
from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

MAX_FILE_SIZE = 1 * 1024 * 1024 # 1 MB
RATE_LIMIT = 2 # requests per hour per IP
rate_limit_window = 60 * 60 # 1 hour

ip_requests = {}  # {ip: [timestamps]}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

CAT_FACE_FOLDER = "cat_faces"
cat_faces = [
    Image.open(os.path.join(CAT_FACE_FOLDER, f)).convert("RGBA")
    for f in os.listdir(CAT_FACE_FOLDER)
    if f.lower().endswith((".png", ".webp"))
]

if not cat_faces:
    raise RuntimeError("No cat face images found.")

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host or "unknown"

@app.middleware("http")
async def rate_limit_and_size_guard(request: Request, call_next):
    client_ip = get_client_ip(request)

    now = time.time()
    timestamps = ip_requests.get(client_ip, [])

    # Drop requests older than window
    timestamps = [ts for ts in timestamps if now - ts < rate_limit_window]
    if len(timestamps) >= RATE_LIMIT:
        return Response("Rate limit exceeded", status_code=429)
    timestamps.append(now)
    ip_requests[client_ip] = timestamps

    
    if request.url.path == "/swap-faces" and request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_FILE_SIZE:
            return Response("File too large", status_code=413)

    return await call_next(request)

@app.post("/swap-faces")
async def swap_faces(file: UploadFile = File(...)):
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")
    
    nparr = np.frombuffer(contents, np.uint8)
    cv_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if cv_img is None:
        return {"error": "Could not read image"}

    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.15,
        minNeighbors=8,
        minSize=(100, 100)
    )
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)[:3]

    human_img_pil = Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)).convert("RGBA")

    for (x, y, w, h) in faces:
        cat_face = random.choice(cat_faces).copy()
        scale = 1.5
        new_w = int(w * scale)
        new_h = int(h * scale)
        cat_resized = cat_face.resize((new_w, new_h))
        offset_x = x + (w - new_w) // 2
        offset_y = y + (h - new_h) // 2
        human_img_pil.paste(cat_resized, (offset_x, offset_y), cat_resized)

    buf = io.BytesIO()
    human_img_pil.convert("RGB").save(buf, format="JPEG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg")
