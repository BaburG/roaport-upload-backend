from fastapi import FastAPI, File, UploadFile, Form, HTTPException
import hashlib
from minio import Minio # type: ignore
from minio.error import S3Error # type: ignore
import uuid
import os
import tempfile
from mimetypes import guess_extension
import sys
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
import json

app = FastAPI()

minio_client = Minio("localhost:9000",
    access_key="cuUg1gWlOuzIDDVDyhhI",
    secret_key="Blibf18yHsUZkDTjlIb3a2UvZNrcqMtz9Vcx3m2Z",
    secure=False)

BUCKET_NAME = "local-test-bucket"

DATA_FILE = "data.json"

# Set up templates directory
templates = Jinja2Templates(directory="templates")


def upload_to_blob(file_path: str, destination_file: str):
    # Make the bucket if it doesn't exist.
    if not minio_client.bucket_exists(BUCKET_NAME):
        minio_client.make_bucket(BUCKET_NAME)
        print("Created bucket", BUCKET_NAME)

    # Upload the file
    minio_client.fput_object(BUCKET_NAME, destination_file, file_path)
    print(f"{file_path} successfully uploaded as {destination_file} to bucket {BUCKET_NAME}")
    
    # Generate a presigned URL
    link = minio_client.get_presigned_url(
        "GET", BUCKET_NAME, destination_file
    )
    return link

def save_metadata(name: str, location: str, link: str):
    # Load existing data or initialize a new list
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    else:
        data = []

    # Append new metadata
    data.append({"name": name, "location": location, "link": link})

    # Save back to the JSON file
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


@app.post("/upload/")
async def upload_file(
    file: UploadFile = File(...),
    hash: str = Form(...),
    location: str = Form(...),
    name: str = Form(...)
):
    
    # Read file content if needed
    file_content = await file.read()
    file_hash = hashlib.sha256(file_content).hexdigest() 


    # Validate file type
    allowed_content_types = ["image/png", "image/jpeg"]
    if file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=415, detail=f"Unsupported file type: {file.content_type}"
        )
    
    # Validate filename extension matches MIME type
    expected_extension = guess_extension(file.content_type) or ""
    if not file.filename.endswith(expected_extension):
        raise HTTPException(
            status_code=400, detail=f"Filename extension does not match file type: expected {expected_extension}"
        )
    
    # # Validate file size from headers (client-provided)
    # max_file_size = 10 * 1024 * 1024  # 10 MB
    # content_length = file.headers.get("content-length")
    # print(content_length)
    # if content_length and int(content_length) > max_file_size:
    #     raise HTTPException(
    #         status_code=413, detail="File size exceeds the 10MB limit"
    #     )
    
    

    if file_hash != hash:
        print(file_hash)
        raise HTTPException(status_code=400, detail="Hash mismatch")

    # Write file to a temporary location
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    # Generate a unique filename
    destination_file = f"{uuid.uuid4()}{expected_extension}"

    try:
        link = upload_to_blob(tmp_path, destination_file)
        save_metadata(name, location, link)
    except S3Error as exc:
        print("An error occurred while uploading to MinIO:", exc)
        raise HTTPException(status_code=500, detail="Failed to upload file to blob storage")
    finally:
        # Clean up the temporary file
        os.remove(tmp_path)

    # Do something with the file and parameters
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "file_hash": file_hash,
        "reported_hash": hash,
        "location": location,
        "name": name,
    }


@app.get("/view", response_class=HTMLResponse)
async def show_images(request: Request):
    # Load the JSON file
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    else:
        data = []

    # Render the template with data
    return templates.TemplateResponse(request, "index.html", {"request": request, "images": data})
