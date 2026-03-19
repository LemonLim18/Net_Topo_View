import asyncio
import unittest


import ansible.api as ansible_api


class TestPreviewIntentStaticRouteMissingFields(unittest.TestCase):
    def test_preview_intent_raises_422_with_questions_before_generation(self):
        # Ensure tree logic doesn't depend on DB access.
        old_db = getattr(ansible_api, "_db", None)
        ansible_api._db = None

        def _fake_preview_parse_and_rag(*, intent_description, target_hosts, vendor, feature, provider, model, ollama_base_url):
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

            with self.assertRaises(Exception) as ctx:
                asyncio.run(_run())

            exc = ctx.exception
            status = getattr(exc, "status_code", None)
            self.assertEqual(status, 422)
            detail = getattr(exc, "detail", None) or {}
            missing = detail.get("missing_fields") or []
            questions = detail.get("questions") or []
            self.assertIn("destination", missing)
            self.assertIn("next_hop", missing)
            self.assertTrue(any(q.get("field") == "destination" for q in questions))
            self.assertTrue(any(q.get("field") == "next_hop" for q in questions))
        finally:
            ansible_api._preview_parse_and_rag = old_fn
            ansible_api._db = old_db


if __name__ == "__main__":
    unittest.main()

