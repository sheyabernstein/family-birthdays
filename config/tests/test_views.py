def test_robots_txt_disallows_everything(client):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain"
    assert response.content == b"User-agent: *\nDisallow: /\n"
