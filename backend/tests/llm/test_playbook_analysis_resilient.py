import asyncio
import pytest
from llm import api as llm_api
from llm.api import PlaybookAnalysisRequest, PlaybookAnalysisResponse

class DummyProvider:
    def __init__(self, response_content: str):
        self.response_content = response_content

    async def generate(self, messages, temperature=0.2):
        class DummyResponse:
            content = self.response_content
        return DummyResponse()

class DummyAgent:
    def __init__(self, provider):
        self._provider = provider

def test_playbook_analyze_dict_explanation(monkeypatch):
    # Setup LLM response returning dictionary for explanation
    mock_json = """{
        "explanation": {
            "Scope": "Cisco IOS OSPF config",
            "Impact Assessment": ["Configure router ospf 1", "Advertise network 192.168.1.0"]
        },
        "rating": 9,
        "safety_level": "Safe",
        "findings": ["[✓] Scope is safe", "[✓] Standard OSPF configuration"]
    }"""
    
    agent = DummyAgent(DummyProvider(mock_json))
    monkeypatch.setattr(llm_api, "_get_agent", lambda: agent)
    
    request = PlaybookAnalysisRequest(playbook_content="fake playbook content")
    response = asyncio.run(llm_api.analyze_playbook(request))
    
    assert isinstance(response, PlaybookAnalysisResponse)
    assert response.rating == 9
    assert response.safety_level == "Safe"
    assert "Scope" in response.explanation
    assert "Impact Assessment" in response.explanation
    assert "- **Scope**:" in response.explanation
    assert "  - Configure router ospf 1" in response.explanation
    assert "  - Advertise network 192.168.1.0" in response.explanation

def test_playbook_analyze_list_explanation(monkeypatch):
    # Setup LLM response returning a list for explanation
    mock_json = """{
        "explanation": [
            "Verify that inventory hosts belong to vendor_cisco_ios",
            "Apply networks to OSPF"
        ],
        "rating": 10,
        "safety_level": "Safe",
        "findings": ["[✓] No warnings"]
    }"""
    
    agent = DummyAgent(DummyProvider(mock_json))
    monkeypatch.setattr(llm_api, "_get_agent", lambda: agent)
    
    request = PlaybookAnalysisRequest(playbook_content="fake playbook content")
    response = asyncio.run(llm_api.analyze_playbook(request))
    
    assert isinstance(response, PlaybookAnalysisResponse)
    assert response.rating == 10
    assert response.safety_level == "Safe"
    assert "- Verify that inventory hosts" in response.explanation
    assert "- Apply networks to OSPF" in response.explanation

def test_playbook_analyze_with_prompt_request(monkeypatch):
    captured_messages = []
    
    class CapturedProvider:
        async def generate(self, messages, temperature=0.2):
            nonlocal captured_messages
            captured_messages = messages
            class DummyResponse:
                content = '{"explanation": "ok", "rating": 9, "safety_level": "Safe", "findings": ["[✓] safe"]}'
            return DummyResponse()
            
    agent = DummyAgent(CapturedProvider())
    monkeypatch.setattr(llm_api, "_get_agent", lambda: agent)
    
    request = PlaybookAnalysisRequest(
        playbook_content="fake playbook content",
        prompt_request="Configure OSPF (Cisco IOS)"
    )
    response = asyncio.run(llm_api.analyze_playbook(request))
    
    assert response.rating == 9
    # The user prompt is the last message
    user_msg = captured_messages[-1].content
    assert "Requested Change / Prompt Intent: Configure OSPF (Cisco IOS)" in user_msg

def test_playbook_analyze_malformed_json_regex_repair(monkeypatch):
    # JSON has missing braces/brackets at the end (unterminated JSON)
    malformed_json = """{
        "explanation": "This configures OSPF",
        "rating": 6,
        "safety_level": "Warning",
        "findings": [
            "[⚠️ CRITICAL WARNING] Scope is fine",
            "[✓] Standard modules"
        ]
    """
    
    agent = DummyAgent(DummyProvider(malformed_json))
    monkeypatch.setattr(llm_api, "_get_agent", lambda: agent)
    
    request = PlaybookAnalysisRequest(playbook_content="hosts: Acc_Cisco_2\ntasks:\n  - name: test")
    response = asyncio.run(llm_api.analyze_playbook(request))
    
    assert isinstance(response, PlaybookAnalysisResponse)
    assert response.rating == 6
    assert response.safety_level == "Warning"
    assert "This configures OSPF" in response.explanation
    assert len(response.findings) >= 2
    assert response.findings[0] == "[⚠️ CRITICAL WARNING] Scope is fine"

def test_playbook_analyze_total_failure_static_fallback(monkeypatch):
    # LLM returns completely garbage text (not even JSON keys)
    garbage_response = "Sorry, I am not able to process this request right now."
    
    agent = DummyAgent(DummyProvider(garbage_response))
    monkeypatch.setattr(llm_api, "_get_agent", lambda: agent)
    
    playbook = """
- name: Configure OSPF (Cisco IOS)
  hosts: "Acc_Cisco_2"
  gather_facts: no
  ignore_unreachable: true
  tasks:
    - name: Configure OSPF process
      cisco.ios.ios_config:
        parents: "router ospf 1"
        lines:
          - "network 192.168.1.0 0.0.0.255 area 0"
    """
    
    request = PlaybookAnalysisRequest(playbook_content=playbook)
    response = asyncio.run(llm_api.analyze_playbook(request))
    
    assert isinstance(response, PlaybookAnalysisResponse)
    # The static audit should detect ignore_unreachable and set rating to 9 (no penalty)
    assert response.rating == 9
    assert response.safety_level == "Safe"
    assert "Acc_Cisco_2" in response.explanation
    # It should have findings from the static audit
    assert any("Playbook target scope: Acc_Cisco_2" in f for f in response.findings)
    assert any("ignore_unreachable" in f for f in response.findings)

def test_playbook_analyze_operational_impact(monkeypatch):
    # Test Normal Operational Level
    normal_pb = "hosts: SW1\ntasks:\n  - name: Add VLAN\n    cisco.ios.ios_vlans:\n      vlan_id: 10\n      name: Web"
    res1 = llm_api.run_static_playbook_audit(normal_pb)
    assert res1["operational_rating"] == 10
    assert res1["operational_level"] == "Normal"
    assert "Low risk" in res1["operational_explanation"]

    # Test Alert Operational Level (Interface state shutdown or routing changes)
    alert_pb = "hosts: SW1\ntasks:\n  - name: Shutdown interface\n    cisco.ios.ios_interfaces:\n      name: GigabitEthernet1/0/1\n      enabled: false"
    res2 = llm_api.run_static_playbook_audit(alert_pb)
    assert res2["operational_rating"] == 7
    assert res2["operational_level"] == "Alert"
    assert "Medium risk" in res2["operational_explanation"]

    # Test Warning Operational Level (Management interface VTY/console or SSH aaa reload)
    warning_pb = "hosts: SW1\ntasks:\n  - name: Configure lines\n    cisco.ios.ios_config:\n      lines:\n        - line vty 0 4\n        - transport input none"
    res3 = llm_api.run_static_playbook_audit(warning_pb)
    assert res3["operational_rating"] == 4
    assert res3["operational_level"] == "Warning"
    assert "High risk" in res3["operational_explanation"]


def test_playbook_analyze_with_operational_rating_from_llm(monkeypatch):
    mock_json = """{
        "explanation": "This is a safe configuration playbook",
        "rating": 10,
        "safety_level": "Safe",
        "findings": ["[✓] Standard VLAN assignment"],
        "operational_rating": 7,
        "operational_level": "Alert",
        "operational_explanation": "Alert operational impact: interface shutdown is configured."
    }"""
    
    agent = DummyAgent(DummyProvider(mock_json))
    monkeypatch.setattr(llm_api, "_get_agent", lambda: agent)
    
    request = PlaybookAnalysisRequest(playbook_content="fake playbook content")
    response = asyncio.run(llm_api.analyze_playbook(request))
    
    assert isinstance(response, PlaybookAnalysisResponse)
    assert response.operational_rating == 7
    assert response.operational_level == "Alert"
    assert response.operational_explanation == "Alert operational impact: interface shutdown is configured."



