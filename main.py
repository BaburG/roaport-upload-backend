from fastapi import FastAPI, File, UploadFile, Form, HTTPException
import hashlib
from minio import Minio
from minio.error import S3Error
import uuid
import os
import tempfile

app = FastAPI()

minio_client = Minio("localhost:9000",
    access_key="cuUg1gWlOuzIDDVDyhhI",
    secret_key="Blibf18yHsUZkDTjlIb3a2UvZNrcqMtz9Vcx3m2Z",
    secure=False)

BUCKET_NAME = "local-test-bucket"


def upload_to_blob(file_path: str, destination_file: str):
    # Make the bucket if it doesn't exist.
    if not minio_client.bucket_exists(BUCKET_NAME):
        minio_client.make_bucket(BUCKET_NAME)
        print("Created bucket", BUCKET_NAME)

    # Upload the file
    minio_client.fput_object(BUCKET_NAME, destination_file, file_path)
    print(f"{file_path} successfully uploaded as {destination_file} to bucket {BUCKET_NAME}")


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

    if file_hash != hash:
        raise HTTPException(status_code=400, detail="Hash mismatch")

    # Write file to a temporary location
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    # Generate a unique filename
    destination_file = f"{uuid.uuid4()}.png"

    try:
        upload_to_blob(tmp_path, destination_file)
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
