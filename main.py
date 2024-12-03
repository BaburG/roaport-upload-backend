from fastapi import FastAPI, File, UploadFile, Form
import hashlib

app = FastAPI()

@app.get('/')
async def hello_world():
    return "hello world"
