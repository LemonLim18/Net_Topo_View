import asyncio
import unittest


import ansible.api as ansible_api


class TestPreviewIntentStaticRouteMissingFields(unittest.TestCase):
    def test_preview_intent_returns_params_disambiguation_before_generation(self):
        # Ensure tree logic doesn't depend on DB access.
        old_db = getattr(ansible_api, "_db", None)
        ansible_api._db = None

        def _fake_preview_parse_and_rag(
            *,
            intent_description,
            target_hosts,
            vendor,
            feature,
            provider,
            model,
            ollama_base_url,
            request_params=None,
            skip_rag=False,
        ):
            parsed_intent = {
                "action": "configure_static_route",
                "feature": "static_route",
                "parameters": {
                    "destination": "",
                    "next_hop": None,
                },
                "resolved_devices": ["SW1"],
                "device_deduction": {},
                "disambiguation": None,
            }
            return (
                ["static_route"],
                [],
                parsed_intent,
                False,  # policy_approved
                [],  # reasoning
                ["SW1"],  # resolved_hosts
            )

        old_fn = ansible_api._preview_parse_and_rag
        ansible_api._preview_parse_and_rag = _fake_preview_parse_and_rag

        try:
            req = ansible_api.PreviewIntentRequest(
                intent_description="Add a default static route",
                target_hosts=["SW1"],
                vendor="cisco_ios",
                feature="static_route",
                params=None,
            )

            async def _run():
                return await ansible_api.preview_intent(req, user={"username": "tester", "role": "admin"})

            res = asyncio.run(_run())
            pdis = res.get("params_disambiguation") or {}
            fields = pdis.get("fields") or []
            field_names = [q.get("field") for q in fields if isinstance(q, dict)]
            # Default routes should auto-fill the destination (0.0.0.0/0),
            # so we only ask for next-hop.
            self.assertNotIn("destination", field_names)
            self.assertIn("next_hop", field_names)
        finally:
            ansible_api._preview_parse_and_rag = old_fn
            ansible_api._db = old_db


if __name__ == "__main__":
    unittest.main()

