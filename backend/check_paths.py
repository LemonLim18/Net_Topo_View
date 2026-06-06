import sys
import os
import llm.embedder
import rag.auto_embedder

print(f"Python Executable: {sys.executable}")
print(f"Current Working Directory: {os.getcwd()}")
print(f"llm.embedder file: {llm.embedder.__file__}")
print(f"rag.auto_embedder file: {rag.auto_embedder.__file__}")

try:
    print(f"llm.embedder._EMBED_MODEL: {llm.embedder._EMBED_MODEL}")
except AttributeError:
    print("llm.embedder._EMBED_MODEL not found")

print("\nFull sys.path:")
for path in sys.path:
    print(f"  {path}")
