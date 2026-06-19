import httpx
import json

try:
    resp = httpx.get("http://localhost:8000/api/workflows/8e8afc17-9157-435d-98f3-f106a672201d", timeout=5)
    with open("status.json", "w") as f:
        json.dump(resp.json(), f, indent=2)
except Exception as e:
    open("status.json", "w").write(str(e))
