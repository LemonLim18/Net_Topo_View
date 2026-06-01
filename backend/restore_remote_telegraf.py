import paramiko
import shlex

remote_host = "192.168.1.250"
remote_port = 22
remote_user = "sdn_mn"
remote_pass = "Elephant18!"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    print(f"Connecting to {remote_host}...")
    client.connect(
        hostname=remote_host,
        port=remote_port,
        username=remote_user,
        password=remote_pass,
        timeout=15
    )
    print("Connected successfully!")
    
    # Define paths
    backup_file = "/etc/telegraf/telegraf.conf.bak-20260601144237"
    target_file = "/etc/telegraf/telegraf.conf"
    
    # Restore command
    restore_cmd = f"sudo cp {shlex.quote(backup_file)} {shlex.quote(target_file)}"
    print(f"Running: {restore_cmd}")
    
    stdin, stdout, stderr = client.exec_command(restore_cmd, get_pty=True)
    # Feed sudo password
    stdin.write(remote_pass + "\n")
    stdin.flush()
    
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode()
    err = stderr.read().decode()
    
    print("Exit Code:", exit_code)
    print("Stdout:", out)
    print("Stderr:", err)
    
    if exit_code == 0:
        print("Restoration completed successfully!")
    else:
        print("Restoration failed!")

except Exception as e:
    print("Error:", e)
finally:
    client.close()
