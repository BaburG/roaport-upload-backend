from fastapi import FastAPI, File, UploadFile, Form, HTTPException
import hashlib
import boto3
import uuid
import os
import tempfile
from mimetypes import guess_extension
import sys
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
import json
from dotenv import load_dotenv
from botocore.exceptions import ClientError
import psycopg2
from contextlib import asynccontextmanager


load_dotenv()


BUCKET_NAME = "cloud-test-bucket"

DATA_FILE = "data.json"

# Set up templates directory
templates = Jinja2Templates(directory="templates")
     

@asynccontextmanager
async def lifespan(app: FastAPI):
    global cnx
    cnx = psycopg2.connect(
        user=os.getenv("AZURE_SQL_USERNAME"),
        password=os.getenv("AZURE_SQL_PASSWORD"),
        host="roaport-sql.postgres.database.azure.com",
        port=5432,
        database="postgres"
    )
    cnx.autocommit = False  # Disable auto-commit for manual transaction management
    print("Connected to azureSQL successfully")

    global objectStorageClient
    objectStorageClient = boto3.client('s3',
                              endpoint_url=os.getenv("R2_ENDPOINT_URL"),
                              aws_access_key_id=os.getenv("R2_ACCESS_KEY"),
                              aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"))
    print("Successfully connected to Cloudflare R2")
    
    yield
    
    print("Closing connection to azureSQL")
    if cnx:
        cnx.close()


def save_metadata_to_db(name: str, longitude: float, latitude: float, bucket_name: str, file_name: str):
    global cnx
    try:
        with cnx.cursor() as cursor:
            # Insert into 'reports' table
            query_reports = """
                INSERT INTO reports (name, longitude, latitude, bucket_name, file_name)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
            """
            cursor.execute(query_reports, (name, longitude, latitude, bucket_name, file_name))
            report_id = cursor.fetchone()[0]

            # Insert or update 'upload_scoreboard' table
            query_scoreboard = """
                INSERT INTO upload_scoreboard (id, upload_count)
                VALUES (%s, 1)
                ON CONFLICT (id) DO UPDATE
                SET upload_count = upload_scoreboard.upload_count + 1;
            """
            cursor.execute(query_scoreboard, (report_id,))

        # Commit the transaction
        cnx.commit()
        return report_id
    except Exception as e:
        # Rollback in case of an error
        cnx.rollback()
        print(f"Error while saving metadata to database: {e}")
        raise HTTPException(status_code=500, detail="Failed to save metadata to database")



app = FastAPI(lifespan=lifespan)

def upload_to_blob(file_path: str, destination_file: str):
    # Check if the bucket exists
    try:
        objectStorageClient.head_bucket(Bucket=BUCKET_NAME)
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            # Bucket doesn't exist, create it
            s3_client.create_bucket(Bucket=BUCKET_NAME)
            print("Created bucket", BUCKET_NAME)
        else:
            print(f"Error checking bucket: {e}")
            raise

    # Upload the file
    #print("pass bucket test")
    objectStorageClient.upload_file(file_path, BUCKET_NAME, destination_file)
    print(f"{file_path} successfully uploaded as {destination_file} to bucket {BUCKET_NAME}")
    
    # Generate a presigned URL
    link = objectStorageClient.generate_presigned_url(
        'get_object',
        Params={'Bucket': BUCKET_NAME, 'Key': destination_file},
        ExpiresIn=3600  # Link expires in 1 hour
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
    #hash: str = Form(...),
    location: str = Form(...),
    name: str = Form(...)
):
    
    # Read file content if needed
    file_content = await file.read()
    file_hash = hashlib.sha256(file_content).hexdigest()

    print(hashlib.sha256(file_content).hexdigest())
    # Validate file type
    allowed_content_types = ["image/png", "image/jpeg"]
    if file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=415, detail=f"Unsupported file type: {file.content_type}"
        )
    print(hashlib.sha256(file_content).hexdigest())
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
    
    
    # print(hashlib.sha256(file_content).hexdigest())
    # if file_hash != hash:
    #     print(file_hash)
    #     raise HTTPException(status_code=400, detail="Hash mismatch")

    # Write file to a temporary location
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    # Generate a unique filename
    destination_file = f"{uuid.uuid4()}{expected_extension}"

    try:
        link = upload_to_blob(tmp_path, destination_file)
        link = f'https://pub-7a565b2e83b14035b5d98e027dae5d16.r2.dev/{destination_file}'
        #save_metadata(name, location, link)
        save_metadata_to_db(name, 0.0, 0.0, BUCKET_NAME, destination_file)
        
    except:
        print("An error occurred while uploading to R2:")
        raise HTTPException(status_code=500, detail="Failed to upload file to blob storage")
    finally:
        # Clean up the temporary file
        os.remove(tmp_path)

    # Do something with the file and parameters
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "file_hash": file_hash,
        #"reported_hash": hash,
        "location": location,
        "name": name,
    }


def fetch_image_data():
    """
    Fetches all image metadata from the database.

    Returns:
        List[Dict]: A list of dictionaries containing image metadata.
    """
    global cnx
    try:
        with cnx.cursor() as cursor:
            # Query all records from the 'reports' table
            query = """
                SELECT id, name, longitude, latitude, bucket_name, file_name, date_created
                FROM reports
                ORDER BY date_created DESC;
            """
            cursor.execute(query)
            rows = cursor.fetchall()

            # Transform query result into a list of dictionaries
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "longitude": row[2],
                    "latitude": row[3],
                    "bucket_name": row[4],
                    "file_name": row[5],
                    "date_created": row[6].isoformat(),
                    "link": f"https://img.roaport.com/{row[5]}"
                }
                for row in rows
            ]
    except Exception as e:
        print(f"Error fetching data from database: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch data from database")


@app.get("/view", response_class=HTMLResponse)
async def show_images(request: Request):
    # Fetch image data from the database
    data = fetch_image_data()
    # for img in data:
    #     print(img)
    #     img["link"] = f"https://e16d722126ccef480a24b7cc683d3e35.r2.cloudflarestorage.com/cloud-test-bucket/{img['file_name']}"  # Link expires in 1 hour
    # Render the template with the fetched data
    return templates.TemplateResponse("index.html", {"request": request, "images": data})
