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
from datetime import datetime
import pika
import logging


load_dotenv()

# Configure logging for better error tracking
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        host=os.getenv("AZURE_SQL_HOST"),
        port=5432,
        database="roaport_prod"
    )
    cnx.autocommit = False  # Disable auto-commit for manual transaction management
    print("Connected to azureSQL successfully")

    global objectStorageClient
    objectStorageClient = boto3.client('s3',
                              endpoint_url=os.getenv("R2_ENDPOINT_URL"),
                              aws_access_key_id=os.getenv("R2_ACCESS_KEY"),
                              aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"))
    print("Successfully connected to Cloudflare R2")
    
    # Setup RabbitMQ connection
    global rabbitmq_connection, rabbitmq_channel
    try:
        credentials = pika.PlainCredentials(
            os.getenv("RABBITMQ_USER"), 
            os.getenv("RABBITMQ_PASS")
        )
        connection_params = pika.ConnectionParameters(
            host=os.getenv("RABBITMQ_HOST"),
            port=int(os.getenv("RABBITMQ_PORT", 5672)),
            credentials=credentials,
            heartbeat=600,  # Keep connection alive with heartbeat
            blocked_connection_timeout=300,  # Timeout for blocked connections
            connection_attempts=3,  # Number of connection attempts
            retry_delay=2  # Delay between connection attempts
        )
        rabbitmq_connection = pika.BlockingConnection(connection_params)
        rabbitmq_channel = rabbitmq_connection.channel()
        
        # Declare the queue to ensure it exists
        queue_name = os.getenv("RABBITMQ_QUEUE")
        rabbitmq_channel.queue_declare(queue=queue_name, durable=True)
        
        print("Successfully connected to RabbitMQ")
    except Exception as e:
        print(f"Failed to connect to RabbitMQ: {e}")
        logger.warning(f"RabbitMQ connection failed during startup: {e}")
        rabbitmq_connection = None
        rabbitmq_channel = None
    
    yield
    
    print("Closing connection to azureSQL")
    if cnx:
        cnx.close()
    
    print("Closing connection to RabbitMQ")
    if rabbitmq_connection and not rabbitmq_connection.is_closed:
        rabbitmq_connection.close()


def save_metadata_to_db(name: str, longitude: float, latitude: float, bucket_name: str, file_name: str, username: str, type: str, detail: str):
    global cnx
    try:
        with cnx.cursor() as cursor:
            # Insert into 'reports' table
            query_reports = """
                INSERT INTO reports (name, longitude, latitude, bucket_name, file_name, username, type, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """
            cursor.execute(query_reports, (name, longitude, latitude, bucket_name, file_name, username.strip(), type.lower(), detail))
            report_id = cursor.fetchone()[0]

        # Commit the transaction
        cnx.commit()
        return report_id
    except Exception as e:
        # Rollback in case of an error
        cnx.rollback()
        print(f"Error while saving metadata to database: {e}")
        raise HTTPException(status_code=500, detail="Failed to save metadata to database")


def publish_to_rabbitmq(message: dict):
    """
    Publishes a message to the RabbitMQ queue with connection recovery.
    
    Args:
        message (dict): The message to publish containing type, id, image_url, and report_id
    """
    global rabbitmq_channel, rabbitmq_connection
    
    # Function to establish/re-establish connection
    def ensure_connection():
        global rabbitmq_channel, rabbitmq_connection
        try:
            # Check if connection exists and is open
            if not rabbitmq_connection or rabbitmq_connection.is_closed:
                logger.info("Establishing new RabbitMQ connection...")
                credentials = pika.PlainCredentials(
                    os.getenv("RABBITMQ_USER"), 
                    os.getenv("RABBITMQ_PASS")
                )
                connection_params = pika.ConnectionParameters(
                    host=os.getenv("RABBITMQ_HOST"),
                    port=int(os.getenv("RABBITMQ_PORT", 5672)),
                    credentials=credentials,
                    heartbeat=600,  # Add heartbeat to keep connection alive
                    blocked_connection_timeout=300,
                )
                rabbitmq_connection = pika.BlockingConnection(connection_params)
                
            # Check if channel exists and is open
            if not rabbitmq_channel or rabbitmq_channel.is_closed:
                logger.info("Creating new RabbitMQ channel...")
                rabbitmq_channel = rabbitmq_connection.channel()
                # Declare the queue to ensure it exists
                queue_name = os.getenv("RABBITMQ_QUEUE")
                rabbitmq_channel.queue_declare(queue=queue_name, durable=True)
                
            return True
        except Exception as e:
            logger.error(f"Failed to establish RabbitMQ connection: {e}")
            rabbitmq_connection = None
            rabbitmq_channel = None
            return False
    
    # Ensure we have a valid connection
    if not ensure_connection():
        logger.error("Unable to establish RabbitMQ connection")
        return False
    
    try:
        queue_name = os.getenv("RABBITMQ_QUEUE")
        rabbitmq_channel.basic_publish(
            exchange='',
            routing_key=queue_name,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Make message persistent
                content_type='application/json'
            )
        )
        logger.info(f"Successfully published message to queue {queue_name}: {message}")
        return True
    except (pika.exceptions.ChannelClosed, pika.exceptions.ConnectionClosed) as e:
        logger.warning(f"RabbitMQ connection/channel closed, attempting to reconnect: {e}")
        # Try to reconnect and publish again
        if ensure_connection():
            try:
                rabbitmq_channel.basic_publish(
                    exchange='',
                    routing_key=queue_name,
                    body=json.dumps(message),
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        content_type='application/json'
                    )
                )
                logger.info(f"Successfully published message after reconnection: {message}")
                return True
            except Exception as retry_e:
                logger.error(f"Failed to publish message after reconnection: {retry_e}")
                return False
        else:
            logger.error("Failed to reconnect to RabbitMQ")
            return False
    except Exception as e:
        logger.error(f"Unexpected error publishing to RabbitMQ: {e}")
        return False


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
    name: str = Form(...),
    username: str = Form(...),
    type: str = Form(...),
    description: str = Form(...)
):
    
    print(file, location, name, username, type, description)

    latitude, longitude = location.replace('{"latitude":', '').replace('"longitude":',"").replace("}", "").split(",")
    #print(latitude,longitude)
    
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
        #print("R2 upload Done")
        #link = f'https://pub-7a565b2e83b14035b5d98e027dae5d16.r2.dev/{destination_file}'
        #save_metadata(name, location, link)
        report_id = save_metadata_to_db(name, longitude, latitude, BUCKET_NAME, destination_file, username, type, description)
        #print("SQL write done")
        
        # Publish message to RabbitMQ queue (non-blocking - don't fail the upload if this fails)
        try:
            message = {
                "type": type,
                "id": destination_file,  # image_id is the file name
                "image_url": f"https://img.roaport.com/{destination_file}",
                "report_id": report_id
            }
            publish_to_rabbitmq(message)
        except Exception as rabbitmq_error:
            logger.error(f"Failed to publish to RabbitMQ: {rabbitmq_error}")
            # Continue with the upload process even if RabbitMQ fails
        
    except Exception as e:
        print(f"An error occurred while uploading to R2 or saving to database: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload file to blob storage or save metadata")
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


# @app.get("/view", response_class=HTMLResponse)
# async def show_images(request: Request):
#     # Fetch image data from the database
#     data = fetch_image_data()
#     # for img in data:
#     #     print(img)
#     #     img["link"] = f"https://e16d722126ccef480a24b7cc683d3e35.r2.cloudflarestorage.com/cloud-test-bucket/{img['file_name']}"  # Link expires in 1 hour
#     # Render the template with the fetched data
#     return templates.TemplateResponse("index.html", {"request": request, "images": data})


@app.get("/start")
async def start_endpoint():
    """
    Returns the current server datetime.
    """
    return {"current_datetime": datetime.now()}
