import os
from influxdb_client import InfluxDBClient

INFLUX_URL = "http://192.168.1.250:8086"
INFLUX_TOKEN = "Qn6eDKTrnmLJfppPyak-BcYcL8RmRS_KfGt3LEUVL1c8X8AeZ_OfdW62Jo0WJLZPrynjzXSdOKkNmFBVUkHA9Q=="
INFLUX_ORG = "cognishield"
INFLUX_BUCKET = "network_monitoring"

def get_tree():
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()

    print(f"🌲 {INFLUX_BUCKET}")

    # 1. Get all Measurements
    measure_query = f'import "influxdata/influxdb/schema" schema.measurements(bucket: "{INFLUX_BUCKET}")'
    measurements = query_api.query(query=measure_query)

    for m_table in measurements:
        for m_record in m_table.records:
            measurement = m_record.get_value()
            print(f"└── 📏 Measurement: {measurement}")

            # 2. Get Tag Values (IP Addresses/Hosts) for this measurement
            # Assuming 'agent_host' or 'host' is your IP tag
            tag_query = f'''
            import "influxdata/influxdb/schema"
            schema.measurementTagValues(bucket: "{INFLUX_BUCKET}", measurement: "{measurement}", tag: "agent_host")
            '''
            tags = query_api.query(query=tag_query)
            
            for t_table in tags:
                for t_record in t_table.records:
                    ip = t_record.get_value()
                    print(f"    ├── 🖥️ IP: {ip}")

                    # 3. Get Fields for this measurement
                    field_query = f'''
                    import "influxdata/influxdb/schema"
                    schema.measurementFieldKeys(bucket: "{INFLUX_BUCKET}", measurement: "{measurement}")
                    '''
                    fields = query_api.query(query=field_query)
                    for f_table in fields:
                        for f_record in f_table.records:
                            print(f"    │   └── 📊 Field: {f_record.get_value()}")
    
    client.close()

if __name__ == "__main__":
    get_tree()
