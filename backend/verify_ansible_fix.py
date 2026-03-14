import sys
import os
import logging

logging.basicConfig(level=logging.INFO)
sys.path.append(os.path.join(os.getcwd(), 'backend'))

print("--- DEBUG INFO ---")
print(f"Platform: {sys.platform}")

try:
    import ansible.runner as runner
    print(f"Runner flag _RUNNER_AVAILABLE: {runner._RUNNER_AVAILABLE}")
    print(f"Is 'fcntl' in sys.modules? {'fcntl' in sys.modules}")
    print(f"Is 'pwd' in sys.modules? {'pwd' in sys.modules}")
except Exception as e:
    print(f"Import of ansible.runner failed: {e}")
    import traceback
    traceback.print_exc()

print("--- DIRECT IMPORT TEST ---")
if sys.platform == "win32":
    import types
    for m in ["fcntl", "pwd"]:
        if m not in sys.modules:
            sys.modules[m] = types.ModuleType(m)
            print(f"Mocked {m} directly in this script for test.")

try:
    import ansible_runner
    print("Directly imported ansible_runner package successfully!")
except Exception as e:
    print(f"Direct import of ansible_runner failed: {e}")
    import traceback
    traceback.print_exc()
