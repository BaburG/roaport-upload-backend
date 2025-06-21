# Roaport Upload Backend

This repository contains the source code for the Roaport Upload Backend, a specialized microservice built with **FastAPI**. Its primary role is to provide a robust, high-performance endpoint for handling file uploads from the Roaport mobile app, acting as the main ingestion point for new hazard reports.

## Features

- **High-Performance File Uploads**: A dedicated `POST /upload/` endpoint optimized for handling multipart/form-data requests.
- **Data Validation**:
  - Uses FastAPI's Pydantic integration for automatic validation of incoming form data (location, name, type, etc.).
  - Validates file content to ensure only allowed MIME types (`image/png`, `image/jpeg`) are accepted.
  - Verifies that the file extension matches the content type to prevent mismatches.
- **Cloud Object Storage Integration**:
  - Securely uploads validated image files to a **Cloudflare R2** bucket using the `boto3` (S3-compatible) client.
  - Generates a unique UUID-based filename for each uploaded image to prevent collisions.
- **Database Integration**:
  - Connects to a **PostgreSQL** database (hosted on Azure) using `psycopg2`.
  - Persists all report metadata (location, type, description, username, and the R2 file path) to the `reports` table.
  - Uses manual transaction management (`commit`/`rollback`) to ensure data integrity.
- **Asynchronous Processing with RabbitMQ**:
  - After a successful upload and database write, it publishes a message to a **RabbitMQ** queue.
  - This message contains the `report_id` and image URL, triggering the downstream `roaport-ML` service to begin analysis.
  - This decouples the slow ML processing from the user-facing upload, allowing for an immediate success response to the mobile app.
  - Includes a robust, retry-enabled connection mechanism to the RabbitMQ server.
- **Containerized & Deployable**:
  - Comes with a `Dockerfile` for easy containerization.
  - Includes a GitHub Actions workflow for automated CI/CD to **Azure Container Apps**.

## Tech Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/)
- **Language**: [Python](https://www.python.org/) 3.11
- **Database Connector**: [psycopg2](https://www.psycopg.org/)
- **Object Storage Client**: [Boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) (for S3-compatible APIs)
- **Message Queue Client**: [Pika](https://pika.readthedocs.io/en/stable/)
- **Containerization**: [Docker](https://www.docker.com/)
- **Deployment**: [Azure Container Apps](https://azure.microsoft.com/en-us/products/container-apps) via [GitHub Actions](https://github.com/features/actions)

## API Endpoints

### `POST /upload/`

The primary endpoint for submitting a new hazard report.

- **Request Type**: `multipart/form-data`
- **Form Fields**:
  - `file`: The image file (`image/png` or `image/jpeg`).
  - `location`: A stringified JSON object with `latitude` and `longitude`.
  - `name`: A short title for the report.
  - `username`: The username or anonymous ID of the reporter.
  - `type`: The category of the hazard (e.g., "pothole", "sign").
  - `description`: A detailed description of the hazard.
  - `pushToken` (optional): The user's Expo push notification token.
- **Success Response (200 OK)**:
  ```json
  {
      "filename": "original_filename.png",
      "content_type": "image/png",
      "file_hash": "server_calculated_sha256_hash",
      "location": "{\"latitude\":34.0522,\"longitude\":-118.2437}",
      "name": "test_file_name"
  }
  ```
- **Error Responses**:
  - `415 Unsupported Media Type`: If the file is not a PNG or JPEG.
  - `400 Bad Request`: If the file extension does not match the MIME type.
  - `422 Unprocessable Entity`: If required form fields are missing.
  - `500 Internal Server Error`: For database or R2 connection failures.

### `GET /start/`

A simple health-check endpoint to confirm the server is running.

- **Success Response (200 OK)**:
  ```json
  {
      "current_datetime": "YYYY-MM-DDTHH:MM:SS.ffffff"
  }
  ```

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Docker (optional, for running in a container)
- Access to a PostgreSQL database
- Access to Cloudflare R2 (or another S3-compatible storage)
- Access to a RabbitMQ server

### Environment Variables

Create a `.env` file in the root directory with the following variables:

```env
# Azure SQL Database Credentials
AZURE_SQL_USERNAME=<your_azure_sql_username>
AZURE_SQL_PASSWORD=<your_azure_sql_password>
AZURE_SQL_HOST=<your_azure_sql_host>

# Cloudflare R2 Credentials
R2_ENDPOINT_URL=<your_r2_endpoint_url>
R2_ACCESS_KEY=<your_r2_access_key_id>
R2_SECRET_ACCESS_KEY=<your_r2_secret_access_key>

# RabbitMQ Configuration
RABBITMQ_HOST=<your_rabbitmq_host>
RABBITMQ_PORT=<your_rabbitmq_port>
RABBITMQ_USER=<your_rabbitmq_username>
RABBITMQ_PASS=<your_rabbitmq_password>
RABBITMQ_QUEUE=<your_queue_name>
```

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/roaport-upload-backend.git
    cd roaport-upload-backend
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

### Running the Application

1.  **Run with Uvicorn:**
    ```bash
    uvicorn main:app --reload
    ```
    The application will be available at `http://127.0.0.1:8000`.

2.  **Run with Docker:**
    ```bash
    # Build the Docker image
    docker build -t roaport-upload-backend .

    # Run the container, passing the .env file
    docker run -d --env-file .env -p 8000:8000 roaport-upload-backend
    ```

## Testing

The project includes a test suite using `pytest`.

```bash
pytest
```
The tests in `test_main.py` cover file validation rules and other endpoint logic.
