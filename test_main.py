from fastapi.testclient import TestClient
import hashlib
import random
import string
import os
import tempfile

from main import app

client = TestClient(app)

def test_upload_file_with_matching_hash():
    # Path to the file
    file_path = "test_file/cat.png"
    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()
    calculated_hash = hashlib.sha256(file_content).hexdigest()

    # Simulate a file upload
    response = client.post(
        "/upload/",
        files={"file": ("cat.png", file_content, "image/png")},
        data={
            "hash": calculated_hash,
            "location": "test_location",
            "name": "test_file",
        },
    )

    # Assert the response is successful
    assert response.status_code == 200
    assert response.json()["file_hash"] == calculated_hash
    assert response.json()["reported_hash"] == calculated_hash


def test_upload_file_with_mismatched_hash():
    # Path to the file
    file_path = "test_file/cat.png"
    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()
    mismatched_hash = "83aff88e5e663b65e51d869daf87037ad4949dd987f7da861f908cb59f547000"  # Intentionally incorrect hash

    # Simulate a file upload
    response = client.post(
        "/upload/",
        files={"file": ("cat.png", file_content, "image/png")},
        data={
            "hash": mismatched_hash,
            "location": "test_location",
            "name": "test_file",
        },
    )

    # Assert the response reports a hash mismatch error
    assert response.status_code == 400
    assert response.json() == {"detail": "Hash mismatch"}




def random_string(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def test_upload_file_and_check_view():
    # Generate random name and location
    random_name = random_string()
    random_location = random_string()
    
    # Path to the file
    file_path = "test_file/cat.png"
    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()
    calculated_hash = hashlib.sha256(file_content).hexdigest()

    # Upload the file
    response = client.post(
        "/upload/",
        files={"file": ("cat.png", file_content, "image/png")},
        data={
            "hash": calculated_hash,
            "location": random_location,
            "name": random_name,
        },
    )

    # Assert upload is successful
    assert response.status_code == 200
    assert response.json()["name"] == random_name
    assert response.json()["location"] == random_location

    # Check the /view route
    response_view = client.get("/view")
    assert response_view.status_code == 200
    assert random_name in response_view.text
    assert random_location in response_view.text

def test_upload_file_without_name_or_location():
    # Path to the file
    file_path = "test_file/cat.png"
    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()
    calculated_hash = hashlib.sha256(file_content).hexdigest()

    # Attempt to upload the file without a name or location
    response = client.post(
        "/upload/",
        files={"file": ("cat.png", file_content, "image/png")},
        data={
            "hash": calculated_hash,
            # No name or location provided
        },
    )

    # Assert the server returns a 400 error
    assert response.status_code == 422  # Validation error for missing fields

def test_upload_unsupported_file_type():
    # Path to a text file
    file_path = "test_file/example.txt"
    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()
    calculated_hash = hashlib.sha256(file_content).hexdigest()

    # Attempt to upload the unsupported file type
    response = client.post(
        "/upload/",
        files={"file": ("example.txt", file_content, "text/plain")},
        data={
            "hash": calculated_hash,
            "location": "test_location",
            "name": "test_file",
        },
    )

    # Assert the server rejects the unsupported file type
    assert response.status_code == 415  # Unsupported Media Type
    assert response.json() == {
        "detail": "Unsupported file type: text/plain"
    }


# def test_upload_large_file():
#     # Generate a large file content (e.g., 20MB)
#     large_file_content = b"a" * (20 * 1024 * 1024)  # 20MB
#     calculated_hash = hashlib.sha256(large_file_content).hexdigest()

#     # Attempt to upload the large file
#     response = client.post(
#         "/upload/",
#         files={"file": ("large_file.png", large_file_content, "image/png")},
#         data={
#             "hash": calculated_hash,
#             "location": "test_location",
#             "name": "test_file",
#         },
#     )

#     # Assert the server rejects the large file
#     assert response.status_code == 413  # Payload Too Large
#     assert response.json() == {
#         "detail": "File size exceeds the 5MB limit"
#     }




def test_temporary_file_deletion():
    # Path to the file
    file_path = "test_file/cat.png"
    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()
    calculated_hash = hashlib.sha256(file_content).hexdigest()

    # Create a temporary directory to check cleanup
    temp_dir = tempfile.gettempdir()

    # Upload the file
    response = client.post(
        "/upload/",
        files={"file": ("cat.png", file_content, "image/png")},
        data={
            "hash": calculated_hash,
            "location": "test_location",
            "name": "test_file",
        },
    )

    # Assert upload is successful
    assert response.status_code == 200

    # Check the temp directory for leftover files
    temp_files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
    assert not any("cat" in f for f in temp_files)  # Ensure no temp files remain

def test_upload_file_with_mismatched_extension():
    # Path to the file
    file_path = "test_file/cat.png"
    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()
    calculated_hash = hashlib.sha256(file_content).hexdigest()

    # Attempt to upload with a mismatched filename extension
    response = client.post(
        "/upload/",
        files={"file": ("cat.jpeg", file_content, "image/png")},  # Filename extension doesn't match MIME type
        data={
            "hash": calculated_hash,
            "location": "test_location",
            "name": "test_file",
        },
    )

    # Assert the server rejects the upload
    assert response.status_code == 400
    assert response.json() == {
        "detail": "Filename extension does not match file type: expected .png"
    }
