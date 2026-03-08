import urllib.request
import json

def test_history():
    try:
        url = "http://localhost:8000/api/v1/monitor/metrics/history?metric=interface_traffic&range_minutes=10080"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())
            points = data.get("data", [])
            print(f"✅ History query: {len(points)} points returned")
            if points:
                print(f"Sample: {points[0]}")
    except Exception as e:
        print(f"❌ History query failed: {e}")

def test_summary():
    try:
        url = "http://localhost:8000/api/v1/monitor/metrics/summary"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())
            devices = data.get("devices", [])
            print(f"✅ Summary query: {len(devices)} devices returned")
            for d in devices:
                print(f"Device {d['hostname']}: Errors={d['errors']}, Discards={d['discards']}")
    except Exception as e:
        print(f"❌ Summary query failed: {e}")

if __name__ == "__main__":
    test_history()
    test_summary()
