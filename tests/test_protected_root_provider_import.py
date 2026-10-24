"""Privileged supervisor must not import optional provider code before UID drop."""
from __future__ import annotations

import importlib.machinery
import os
import sys
import types
import unittest
from unittest.mock import patch

from mark_api import protected_product as product


class ProtectedRootProviderImportTests(unittest.TestCase):
    def test_supervisor_checks_sdk_without_executing_provider_module(self):
        accessed = []
        fake_provider = types.ModuleType("openai")
        fake_provider.__spec__ = importlib.machinery.ModuleSpec("openai", loader=None)

        def attribute(name):
            if name == "OpenAI":
                accessed.append(name)
                return lambda *args, **kwargs: None
            raise AttributeError(name)

        fake_provider.__getattr__ = attribute
        key = "synthetic-no-network-provider-test-key"
        with patch.dict(os.environ, {"OPENAI_API_KEY": key}), patch.dict(
            sys.modules, {"openai": fake_provider}
        ):
            self.assertEqual(product._generation_admission(), key)
        self.assertEqual(
            accessed, [],
            "Root supervisor imported optional provider module before UID drop",
        )

    def test_missing_sdk_refuses_fresh_state_before_any_write(self):
        key = "synthetic-no-network-provider-test-key"
        with patch.dict(os.environ, {"OPENAI_API_KEY": key}), patch(
            "mark_api.protected_product._root_admission"
        ), patch(
            "mark_api.protected_product.importlib.util.find_spec", return_value=None
        ), patch(
            "mark_api.protected_product._root_path"
        ) as inspect, patch(
            "mark_api.protected_product._initialize"
        ) as initialize:
            with self.assertRaisesRegex(
                product.ProtectedProductError, "generation SDK unavailable"
            ):
                product.run(
                    root=__import__("pathlib").Path("/tmp/uncreated-protected-fixture"),
                    init_new=True, enable_generation=True,
                )
            inspect.assert_not_called()
            initialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
