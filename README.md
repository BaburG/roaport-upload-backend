# Roaport Upload Backend

## Overview

This project is a FastAPI backend designed to handle file uploads, specifically images. It stores the uploaded files in Cloudflare R2 object storage and saves associated metadata to an Azure SQL (PostgreSQL) database. The application is containerized using Docker and includes a GitHub Actions workflow for automated CI/CD to Azure Container Apps.

## Features

*   **File Upload:**
    *   Endpoint (`POST /upload/`) to upload image files (PNG, JPEG).
    *   File content validation (MIME type).
    *   Filename extension validation against MIME type.
    *   Securely uploads files to Cloudflare R2.
    *   Generates a unique filename for storage.
    *   Calculates and returns the SHA256 hash of the uploaded file.
*   **Metadata Storage:**
    *   Saves metadata (report name, longitude, latitude, bucket name, file name, username, report type, description) to an Azure SQL (PostgreSQL) database.
*   **Image Viewing (Conceptual):**
    *   A `GET /view/` endpoint and corresponding HTML template (`templates/index.html`) are present in the codebase to display uploaded images and their details. This endpoint fetches data from the database.
    *   *(Note: This endpoint is currently commented out in `main.py` but the underlying logic and template exist).*
*   **Health/Status Check:**
    *   Endpoint (`GET /start/`) to confirm the server is running and get the current server datetime.
*   **Database Management:**
    *   Utilizes `psycopg2` for robust PostgreSQL database interactions.
    *   Connection management via FastAPI's lifespan events.
*   **Object Storage Integration:**
    *   Uses `boto3` to interact with Cloudflare R2.
    *   Attempts to create the R2 bucket if it doesn't exist during upload.
*   **Configuration:**
    *   Manages sensitive credentials and settings using a `.env` file.
*   **Containerization:**
    *   Includes a `Dockerfile` for easy building and deployment.
*   **Automated Deployment:**
    *   GitHub Actions workflow for continuous integration and deployment to Azure Container Apps.
    *   Builds and pushes Docker images to Azure Container Registry.
*   **Testing:**
    *   Comprehensive test suite using `pytest` and `TestClient` covering various upload scenarios, error handling, and file validation (see `test_main.py`).

## Technologies Used

*   **Backend:** Python, FastAPI
*   **Database:** Azure SQL (PostgreSQL), `psycopg2`
*   **Object Storage:** Cloudflare R2, `boto3`
*   **Containerization:** Docker
*   **CI/CD:** GitHub Actions, Azure Container Apps, Azure Container Registry
*   **Templating:** Jinja2 (for the conceptual `/view` endpoint)
*   **Testing:** `pytest`
*   **Environment Management:** `python-dotenv`

## Setup and Installation

**Prerequisites:**
*   Python 3.11 or higher
*   Docker (optional, for running in a container)
*   Access to an Azure SQL (PostgreSQL) database
*   Access to Cloudflare R2 (or compatible S3 storage)

**Environment Variables:**
Create a `.env` file in the root directory with the following variables:

```env
# Azure SQL Database Credentials
AZURE_SQL_USERNAME=<your_azure_sql_username>
AZURE_SQL_PASSWORD=<your_azure_sql_password>
AZURE_SQL_HOST=<your_azure_sql_host>
# Note: Database name is 'roaport_prod' (hardcoded in main.py)
# Note: Bucket name for R2 is 'cloud-test-bucket' (hardcoded in main.py)

# Cloudflare R2 Credentials
R2_ENDPOINT_URL=<your_r2_endpoint_url>
R2_ACCESS_KEY=<your_r2_access_key_id>
R2_SECRET_ACCESS_KEY=<your_r2_secret_access_key>
```

**Installation:**
1.  Clone the repository:
    ```bash
    git clone <repository_url>
    cd roaport-upload-backend
    ```
2.  Create and activate a virtual environment:
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```
3.  Install dependencies from the root `requirements.txt`:
    ```bash
    pip install -r requirements.txt
    ```

**Running the Application:**
```bash
uvicorn main:app --reload
```
The application will be available at `http://127.0.0.1:8000`.

**Running Tests:**
```bash
pytest
```
*(Note: Some tests in `test_main.py` expect a `hash` form field in the `/upload/` endpoint, which is currently commented out in `main.py`. These specific tests may require adjustments or re-enabling the `hash` parameter in `main.py` to pass as originally designed.)*

## API Endpoints

*   `POST /upload/`: Uploads an image file and its metadata.
    *   **Form Data:**
        *   `file`: The image file (PNG or JPEG).
        *   `location`: String representing coordinates (e.g., `{"latitude":34.0522,"longitude":-118.2437}`).
        *   `name`: A name or title for the report/image.
        *   `username`: The username of the person submitting the report.
        *   `type`: The type or category of the report (e.g., "pothole", "graffiti").
        *   `description`: A detailed description for the report.
    *   **Success Response (200 OK):**
        ```json
        {
            "filename": "uploaded_file.png",
            "content_type": "image/png",
            "file_hash": "server_calculated_sha256_hash_of_file",
            "location": "{\"latitude\":34.0522,\"longitude\":-118.2437}",
            "name": "test_file_name"
        }
        ```
    *   **Error Responses:**
        *   `400 Bad Request`: For issues like mismatched file extension.
        *   `415 Unsupported Media Type`: If file type is not PNG or JPEG.
        *   `422 Unprocessable Entity`: If required form fields are missing.
        *   `500 Internal Server Error`: For database connection/operation failures or R2 upload failures.

*   `GET /view/`: (Conceptual - currently commented out in `main.py`) Renders an HTML page displaying uploaded images and their metadata.
    *   Fetches data from the Azure SQL database.
    *   Uses `templates/index.html` to display images with their name, location, and creation date. Links are constructed as `https://img.roaport.com/<file_name>`.

*   `GET /start/`: A simple endpoint to check if the server is running and get current server time.
    *   **Success Response (200 OK):**
        ```json
        {
            "current_datetime": "YYYY-MM-DDTHH:MM:SS.ffffff"
        }
        ```

## Deployment

This project is configured for automated deployment to Azure Container Apps via GitHub Actions.
*   The workflow is defined in `.github/workflows/roaport-upload-backend-AutoDeployTrigger-....yml`.
*   On pushes to the `main` branch, the workflow:
    1.  Checks out the code.
    2.  Logs into Azure using credentials stored as GitHub secrets.
    3.  Builds a Docker image using the `Dockerfile` located in the project root.
    4.  Pushes the image to the Azure Container Registry (`roaport.azurecr.io`).
    5.  Deploys the new image to the `roaport-upload-backend` container app within the `roaport-resource-group` resource group.
*   Necessary Azure credentials (`ROAPORTUPLOADBACKEND_AZURE_CREDENTIALS`, `ROAPORTUPLOADBACKEND_REGISTRY_USERNAME`, `ROAPORTUPLOADBACKEND_REGISTRY_PASSWORD`) must be configured in the GitHub repository's secrets.

## Future Enhancements

*   **Implement a RabbitMQ message queue:** To decouple tasks like post-upload processing (e.g., thumbnail generation, advanced image analysis, sending notifications) from the synchronous upload request. This can improve the API's responsiveness and resilience. Upon successful file upload and initial metadata storage, a message could be published to a RabbitMQ queue for asynchronous workers to consume and process further.
