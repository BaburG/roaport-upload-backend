from fastapi import FastAPI, File, UploadFile, Form, HTTPException
import hashlib

app = FastAPI()


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

    # Do something with the file and parameters
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "file_hash": file_hash,
        "reported_hash": hash,
        "location": location,
        "name": name,
    }
