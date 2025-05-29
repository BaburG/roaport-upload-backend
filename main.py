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
import pika.exceptions  # Make sure this is imported
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
    rabbitmq_connection = None  # Initialize as None
    rabbitmq_channel = None     # Initialize as None
    
    # Attempt initial connection (optional here, can be deferred to first publish)
    # For now, let the publish_to_rabbitmq handle the first connection.
    # This avoids holding a connection if no uploads happen for a while after startup.
    print("RabbitMQ connection will be established on first publish")
    
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
    Publishes a message to the RabbitMQ queue with connection recovery and publisher confirms.
    
    Args:
        message (dict): The message to publish containing type, id, image_url, and report_id
        
    Returns:
        bool: True if message was successfully published and confirmed, False otherwise
    """
    global rabbitmq_channel, rabbitmq_connection

    def ensure_connection_and_confirm_mode():
        global rabbitmq_channel, rabbitmq_connection
        try:
            if not rabbitmq_connection or rabbitmq_connection.is_closed:
                logger.info("RabbitMQ: Establishing new connection...")
                credentials = pika.PlainCredentials(
                    os.getenv("RABBITMQ_USER"), 
                    os.getenv("RABBITMQ_PASS")
                )
                connection_params = pika.ConnectionParameters(
                    host=os.getenv("RABBITMQ_HOST"),
                    port=int(os.getenv("RABBITMQ_PORT", 5672)),
                    credentials=credentials,
                    heartbeat=60,  # Shorten heartbeat to 60 seconds
                    blocked_connection_timeout=300,
                )
                rabbitmq_connection = pika.BlockingConnection(connection_params)
                logger.info("RabbitMQ: Connection established.")
            
            if not rabbitmq_channel or rabbitmq_channel.is_closed:
                logger.info("RabbitMQ: Creating new channel...")
                rabbitmq_channel = rabbitmq_connection.channel()
                # Enable transactions instead of publisher confirms
                rabbitmq_channel.tx_select()
                logger.info("RabbitMQ: Channel created and transactions enabled.")
                
                # Declare the queue to ensure it exists
                queue_name = os.getenv("RABBITMQ_QUEUE")
                # Add more queue details for debugging
                try:
                    method = rabbitmq_channel.queue_declare(queue=queue_name, durable=True, passive=False)
                    logger.info(f"RabbitMQ: Queue '{queue_name}' declared successfully. Messages in queue: {method.method.message_count}")
                except Exception as queue_error:
                    logger.error(f"RabbitMQ: Failed to declare queue '{queue_name}': {queue_error}")
                    raise queue_error
                
            return True
        except Exception as e:
            logger.error(f"RabbitMQ: Failed to establish connection/channel: {e}")
            # Reset global vars on failure to ensure full re-init next time
            rabbitmq_connection = None
            rabbitmq_channel = None
            return False

    # Validate message before attempting to publish
    try:
        # Ensure message is JSON serializable and not too large
        message_json = json.dumps(message)
        message_size = len(message_json.encode('utf-8'))
        logger.info(f"RabbitMQ: Message size: {message_size} bytes for report_id: {message.get('report_id', 'unknown')}")
        
        # Check if message is too large (RabbitMQ default max is usually 128MB, but some configs are much smaller)
        if message_size > 1024 * 1024:  # 1MB threshold for warning
            logger.warning(f"RabbitMQ: Large message detected ({message_size} bytes). This might cause issues.")
        
        # Validate message structure
        required_fields = ['type', 'id', 'image_url', 'report_id']
        missing_fields = [field for field in required_fields if field not in message]
        if missing_fields:
            logger.error(f"RabbitMQ: Message missing required fields: {missing_fields}")
            return False
            
        logger.info(f"RabbitMQ: Message validation passed for report_id: {message['report_id']}")
        
    except Exception as validation_error:
        logger.error(f"RabbitMQ: Message validation failed: {validation_error}")
        return False

    MAX_RETRIES = 3
    retry_count = 0
    
    while retry_count < MAX_RETRIES:
        if not ensure_connection_and_confirm_mode():
            logger.error("RabbitMQ: Unable to establish connection and channel for publishing.")
            retry_count += 1
            if retry_count >= MAX_RETRIES: 
                return False  # Exhausted connection retries
            continue  # Try to connect again

        try:
            queue_name = os.getenv("RABBITMQ_QUEUE")
            
            # Log the exact message being sent for debugging
            logger.info(f"RabbitMQ: Attempting to publish message: {json.dumps(message)} to queue '{queue_name}'")
            
            # Publish message (no return value check needed with transactions)
            rabbitmq_channel.basic_publish(
                exchange='',
                routing_key=queue_name,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE,  # Use pika.spec constant
                    content_type='application/json',
                    # Add message ID for tracking
                    message_id=str(message.get('report_id', 'unknown'))
                ),
                mandatory=True  # This will cause an exception if message can't be routed
            )
            
            # Commit the transaction - this blocks until RabbitMQ confirms delivery
            rabbitmq_channel.tx_commit()
            logger.info(f"RabbitMQ: Successfully published and committed message to queue '{queue_name}': {message['report_id']}")
            return True
        
        except pika.exceptions.UnroutableError as e:
            logger.error(f"RabbitMQ: Message unroutable (queue might not exist or be accessible): {e}. Message: {message['report_id']}. This is a permanent error for this message.")
            return False  # Don't retry unroutable messages
        except (pika.exceptions.ChannelClosedByBroker, pika.exceptions.ConnectionClosedByBroker) as e:
            logger.warning(f"RabbitMQ: Channel or Connection closed by broker: {e}. Retrying ({retry_count+1}/{MAX_RETRIES})...")
            # Check if the error gives us more details about why it was closed
            if hasattr(e, 'reply_code') and hasattr(e, 'reply_text'):
                logger.error(f"RabbitMQ: Broker close reason - Code: {e.reply_code}, Text: {e.reply_text}")
            rabbitmq_channel = None  # Force channel re-creation
            rabbitmq_connection = None  # Force connection re-creation
            retry_count += 1
        except (pika.exceptions.ChannelWrongStateError, pika.exceptions.StreamLostError, pika.exceptions.AMQPConnectionError) as e:
            logger.warning(f"RabbitMQ: Connection/Channel error: {e}. Retrying ({retry_count+1}/{MAX_RETRIES})...")
            rabbitmq_channel = None  # Force channel re-creation
            rabbitmq_connection = None  # Force connection re-creation
            retry_count += 1
        except Exception as e:
            logger.error(f"RabbitMQ: Unexpected error publishing: {e}. Retrying ({retry_count+1}/{MAX_RETRIES})...")
            # Log the full exception details
            import traceback
            logger.error(f"RabbitMQ: Full traceback: {traceback.format_exc()}")
            rabbitmq_channel = None
            rabbitmq_connection = None
            retry_count += 1
            
        if retry_count >= MAX_RETRIES:
            logger.error(f"RabbitMQ: Failed to publish message after {MAX_RETRIES} retries: {message['report_id']}")
            return False
            
    return False  # Should not be reached if logic is correct


app = FastAPI(lifespan=lifespan)

def upload_to_blob(file_path: str, destination_file: str):
    # Check if the bucket exists
    try:
        objectStorageClient.head_bucket(Bucket=BUCKET_NAME)
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            # Bucket doesn't exist, create it
            objectStorageClient.create_bucket(Bucket=BUCKET_NAME)  # Fixed: changed s3_client to objectStorageClient
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
        
        # Publish message to RabbitMQ queue with improved error handling
        try:
            message = {
                "type": type,
                "id": destination_file,  # image_id is the file name
                "image_url": f"https://img.roaport.com/{destination_file}",
                "report_id": report_id
            }
            if publish_to_rabbitmq(message):
                logger.info(f"Upload endpoint: RabbitMQ publish successful for report_id: {report_id}")
            else:
                # This is CRITICAL: you now know it failed despite retries
                logger.error(f"Upload endpoint: RabbitMQ publish FAILED for report_id: {report_id}. The upload will still succeed as per design.")
                # Depending on your requirements, you might:
                # - Add to a dead-letter queue/database for later retry by a separate process
                # - Raise an alert
        except Exception as rabbitmq_error:  # Catchall for unexpected errors from publish_to_rabbitmq itself
            logger.error(f"Upload endpoint: Exception during RabbitMQ publish call for report_id {report_id}: {rabbitmq_error}")
        
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
