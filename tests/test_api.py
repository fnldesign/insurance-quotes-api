import json


def test_health_endpoint(app_client):
    resp = app_client.get('/health/')
    assert resp.status_code in (200, 503)
    data = resp.get_json()
    assert 'status' in data
    assert 'database' in data


def test_create_and_list_cotacoes(app_client):
    payload = {
        "nome": "Sr. João Silva",  # title-based gender inference avoids external API call
        "cpf": "12345678901",
        "sexo": "M",
        "dtnasc": "1990-01-01",
        "capital": 10000.0,
        "inicio_vig": "2025-01-01",
        "fim_vig": "2025-12-31"
    }

    # Create
    resp = app_client.post('/cotacoes/', data=json.dumps(payload), content_type='application/json')
    assert resp.status_code == 201
    created = resp.get_json()
    assert 'id' in created
    assert created['premio'] >= 0

    # List
    resp = app_client.get('/cotacoes/')
    assert resp.status_code == 200
    items = resp.get_json()
    assert isinstance(items, list)
    assert any(it['id'] == created['id'] for it in items)

    # Get single
    cid = created['id']
    resp = app_client.get(f'/cotacoes/{cid}')
    assert resp.status_code == 200
    item = resp.get_json()
    assert item['id'] == cid
    # created_at should be a string (serialized)
    if 'created_at' in item:
        assert isinstance(item['created_at'], str)
