import httpx
import json

payload = {
    "workflow_type": "meeting_intelligence",
    "input_data": {
        "content": "Bob from engineering noted that the backend latency issues are caused by missing indexes on the users table. Alice will patch this by Friday. Let us circle back next week."
    }
}

print("Invoking Sentinel-AI Engine via Local API...\n")
response = httpx.post("http://localhost:8000/api/workflows/", json=payload)
print("Response Status Code:", response.status_code)
print("Response JSON Payload:\n")
print(json.dumps(response.json(), indent=2))
