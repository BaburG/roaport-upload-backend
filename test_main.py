from fastapi.testclient import TestClient
import hashlib

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