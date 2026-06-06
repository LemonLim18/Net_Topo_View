# M5: LLM Agent — Implementation Plan

## Goal
Implement a context-aware AI agent that reasons about network topology,
generates CLI configurations, and answers operational questions.

## Architecture
```text
backend/llm/
├── __init__.py           # exports NetworkAgent
├── provider.py           # Abstract LLMProvider ABC
├── gemini_provider.py    # Google Gemini SDK wrapper
├── tools.py              # ToolRegistry (auto-schema from signatures)
├── context.py            # RAG context builder (topology + templates)
├── network_tools.py      # 10 registered tools
├── agent.py              # NetworkAgent (multi-round tool-calling loop)
└── api.py                # FastAPI router (6 endpoints)
```

## Registered Tools (10)
| Tool | Category |
|------|----------|
| `get_topology_summary` | Topology |
| `get_device_details` | Topology |
| `list_all_devices` | Topology |
| `find_path` | Topology |
| `generate_config` | Config (M3) |
| `generate_rollback` | Config (M3) |
| `list_templates` | Config (M3) |
| `get_template_schema` | Config (M3) |
| `diff_configs` | Config (M3) |
| `get_audit_log` | Audit (M14) |

## API Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/chat` | Send message, get AI response |
| POST | `/api/chat/clear` | Clear conversation history |
| GET | `/api/chat/history` | Get conversation history |
| GET | `/api/chat/tools` | List available tools |
| GET | `/api/chat/info` | Agent info (model, status) |
| POST | `/api/chat/refresh` | Refresh network context |

## Integration
To wire into `backend.py`:
```python
from backend.llm.agent import NetworkAgent
from backend.llm.api import router as chat_router, setup_agent

agent = NetworkAgent(db_manager=db)
setup_agent(agent)
app.include_router(chat_router)
```

## Verification
- All imports verified in venv
- 10 tools registered and schema-validated
- Config generation + rollback tested (VLAN template)
- Context builder tested (topology + templates)
