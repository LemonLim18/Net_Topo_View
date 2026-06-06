import redis

# Establish the connection
client = redis.Redis(host='192.168.1.250', port=6379, decode_responses=True)

# Test the connection
print(client.ping())