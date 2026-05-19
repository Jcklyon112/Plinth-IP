from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_list_parcels_returns_three():
    response = client.get("/parcels")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


def test_get_massing_parcel_001():
    response = client.get("/parcels/parcel-001/massing")
    assert response.status_code == 200
    data = response.json()
    assert "coordinates" in data
    assert len(data["coordinates"]) > 0


def test_get_massing_bad_id_returns_404():
    response = client.get("/parcels/bad-id/massing")
    assert response.status_code == 404


def test_picker_returns_200():
    response = client.get("/picker")
    assert response.status_code == 200
